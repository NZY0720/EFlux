"""Integration tests for Tier A3 external guidance ingestion.

PUT /vpps/managed/{id}/guidance steers a managed agent with the caller's own model;
DELETE hands control back to the platform LLM. Clamping is server-side and
authoritative; guidance persists in managed_config and survives a restart.
"""

from __future__ import annotations

import pytest

from eflux.api.main import _rehydrate_managed_vpps
from eflux.api.routers import vpps as vpps_router
from eflux.bridge import InMemoryBus
from eflux.simulator.runner import Simulator

pytestmark = pytest.mark.asyncio


async def _login(client, email="a3@hku.hk") -> tuple[dict, int]:
    r = await client.post("/auth/magic-link", json={"email": email})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    body = r.json()
    return {"Authorization": f"Bearer {body['session_token']}"}, body["user_id"]


async def _managed(client, auth, name="a3-agent") -> int:
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={"name": name, "params": {"pv_kw_peak": 3.0, "battery_kwh": 8.0}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_put_guidance_clamps_flips_source_and_feeds_reflections(client):
    auth, _ = await _login(client)
    managed_id = await _managed(client, auth)

    r = await client.put(
        f"/vpps/managed/{managed_id}/guidance",
        headers=auth,
        json={
            "preferred_modes": ["ladder_sell", "not-a-mode"],
            "mode_pin": "cover_deficit",
            "risk_budget": 5.0,  # server clamps to 1.5
            "price_bias_bps": 500.0,
            "soc_target": 0.3,
            "execution_style": "sell the rich tape",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["guidance_source"] == "external"
    applied = body["applied"]
    assert applied["risk_budget"] == 1.5  # clamped echo, not the raw 5.0
    assert applied["mode_pin"] == "cover_deficit"
    assert applied["price_bias_bps"] == 200.0
    assert applied["preferred_modes"] == ["ladder_sell"]  # unknown mode dropped
    assert applied["ok"] is True

    # The managed listing reports the new steering source.
    r = await client.get("/vpps/managed", headers=auth)
    assert r.json()[0]["guidance_source"] == "external"

    # The audit surfaces the injected guidance: owner performance + public reflections.
    r = await client.get(f"/vpps/managed/{managed_id}/performance", headers=auth)
    styles = [e["execution_style"] for e in r.json()["reflections"]]
    assert "sell the rich tape" in styles
    r = await client.get("/market/reflections")
    assert any(e["execution_style"] == "sell the rich tape" for e in r.json())


async def test_public_reflections_never_expose_lesson_fallback(client):
    auth, _ = await _login(client, "lesson-private@hku.hk")
    managed_id = await _managed(client, auth, "lesson-agent")
    secret = "private alpha should stay owner-only"

    r = await client.put(
        f"/vpps/managed/{managed_id}/guidance",
        headers=auth,
        json={"risk_budget": 0.6, "soc_target": 0.7, "lesson": secret},
    )
    assert r.status_code == 200, r.text

    r = await client.get("/market/reflections")
    public_body = r.json()
    assert secret not in str(public_body)
    assert all(entry.get("lesson") is None for entry in public_body)

    r = await client.get(f"/vpps/managed/{managed_id}/performance", headers=auth)
    private_lessons = [entry["lesson"] for entry in r.json()["reflections"]]
    assert secret in private_lessons


async def test_release_restores_platform_and_is_idempotent(client):
    auth, _ = await _login(client)
    managed_id = await _managed(client, auth)

    r = await client.put(
        f"/vpps/managed/{managed_id}/guidance", headers=auth, json={"risk_budget": 0.4}
    )
    assert r.status_code == 200
    assert r.json()["applied"]["soc_target"] is None
    r = await client.delete(f"/vpps/managed/{managed_id}/guidance", headers=auth)
    assert r.status_code == 204
    r = await client.get("/vpps/managed", headers=auth)
    # The displaced strategist is restored exactly. Tests run with the platform LLM
    # disabled, so hybrid agents are provisioned with NO strategist — release returns
    # to that state ("none"); with a live platform LLM it would read "platform".
    assert r.json()[0]["guidance_source"] == "none"
    # Releasing again is a no-op, not an error.
    r = await client.delete(f"/vpps/managed/{managed_id}/guidance", headers=auth)
    assert r.status_code == 204


async def test_ownership_enforced(client):
    auth_a, _ = await _login(client, "owner-a@hku.hk")
    managed_id = await _managed(client, auth_a)
    auth_b, _ = await _login(client, "owner-b@hku.hk")
    r = await client.put(
        f"/vpps/managed/{managed_id}/guidance", headers=auth_b, json={"risk_budget": 0.1}
    )
    assert r.status_code == 404
    r = await client.delete(f"/vpps/managed/{managed_id}/guidance", headers=auth_b)
    assert r.status_code == 404


async def test_guidance_rate_limited(client):
    auth, _ = await _login(client)
    managed_id = await _managed(client, auth)
    # Burst capacity is 10; the 11th call in a tight loop must 429.
    statuses = []
    for _ in range(11):
        r = await client.put(
            f"/vpps/managed/{managed_id}/guidance", headers=auth, json={"risk_budget": 0.9}
        )
        statuses.append(r.status_code)
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429


async def test_external_guidance_survives_restart(client):
    auth, user_id = await _login(client)
    managed_id = await _managed(client, auth)
    r = await client.put(
        f"/vpps/managed/{managed_id}/guidance",
        headers=auth,
        json={"risk_budget": 0.6, "soc_target": 0.8, "lesson": "persisted steer"},
    )
    assert r.status_code == 200

    # Simulated restart: rehydration re-provisions AND re-applies the external steer,
    # so the platform LLM stays idle and the last guidance still applies.
    fresh = Simulator(bus=InMemoryBus())
    await _rehydrate_managed_vpps(fresh)
    vpp = fresh.my_managed_vpps(user_id)[0]
    assert vpps_router._guidance_source(vpp) == "external"
    g = vpp.agent.strategist.current_guidance()
    assert g is not None
    assert g.risk_budget == 0.6
    assert g.soc_target == 0.8
    assert not hasattr(vpp.agent.strategist, "arefresh")
