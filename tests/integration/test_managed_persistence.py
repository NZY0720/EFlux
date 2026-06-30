"""Managed agents (Tier 0) persist across a simulated restart and can be deleted.

The live market state is ephemeral, but a provisioned managed agent's *definition* is
stored (vpps.is_managed rows) and re-provisioned on startup — see
docs/EXTERNAL_PARTICIPATION.md and api/main._rehydrate_managed_vpps.
"""

from __future__ import annotations

import pytest

from eflux.api.main import _rehydrate_managed_vpps
from eflux.bridge import InMemoryBus
from eflux.simulator.runner import Simulator


async def _login(client) -> tuple[str, int]:
    r = await client.post("/auth/magic-link", json={"email": "persist@hku.hk"})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    body = r.json()
    return body["session_token"], body["user_id"]


@pytest.mark.asyncio
async def test_managed_agent_persists_across_restart_and_deletes(client):
    sess, user_id = await _login(client)
    auth = {"Authorization": f"Bearer {sess}"}

    # Provision a managed agent — persisted to the DB.
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={
            "name": "persistent-mgd",
            "params": {"pv_kw_peak": 3.0, "battery_kwh": 8.0},
            "persona": "Cautious.",
            "agent_params": {"demand_beta": 0.4},
        },
    )
    assert r.status_code == 201, r.text
    managed_id = r.json()["id"]

    # Simulate a restart: a fresh Simulator re-provisions managed agents from the DB.
    fresh = Simulator(bus=InMemoryBus())
    await _rehydrate_managed_vpps(fresh)
    mine = fresh.my_managed_vpps(user_id)
    assert [v.name for v in mine] == ["persistent-mgd"]
    assert mine[0].managed_def_id == managed_id
    assert type(mine[0].agent).__name__ == "HybridPolicyAgent"

    # Delete it: removed from the live sim and the DB.
    r = await client.delete(f"/vpps/managed/{managed_id}", headers=auth)
    assert r.status_code == 204, r.text
    r = await client.get("/vpps/managed", headers=auth)
    assert r.json() == []

    # A subsequent restart finds nothing to rehydrate.
    fresh2 = Simulator(bus=InMemoryBus())
    await _rehydrate_managed_vpps(fresh2)
    assert fresh2.my_managed_vpps(user_id) == []


@pytest.mark.asyncio
async def test_managed_agents_excluded_from_passive_vpp_list(client):
    """A managed agent must not leak into GET /vpps (passive list) or its DELETE path."""
    sess, _ = await _login(client)
    auth = {"Authorization": f"Bearer {sess}"}

    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={"name": "mgd-only", "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0}},
    )
    assert r.status_code == 201, r.text
    managed_id = r.json()["id"]

    # Passive list excludes managed agents.
    r = await client.get("/vpps", headers=auth)
    assert r.status_code == 200
    assert all(v["name"] != "mgd-only" for v in r.json())

    # The passive DELETE must not touch a managed row (404, agent stays).
    r = await client.delete(f"/vpps/{managed_id}", headers=auth)
    assert r.status_code == 404, r.text
    r = await client.get("/vpps/managed", headers=auth)
    assert any(v["name"] == "mgd-only" for v in r.json())


@pytest.mark.asyncio
async def test_managed_agent_preferences_patch(client):
    sess, user_id = await _login(client)
    auth = {"Authorization": f"Bearer {sess}"}

    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={
            "name": "tunable",
            "params": {"pv_kw_peak": 3.0, "battery_kwh": 8.0},
            "persona": "Original brief.",
            "agent_params": {"demand_beta": 0.3},
        },
    )
    assert r.status_code == 201, r.text
    managed_id = r.json()["id"]

    # Patch DER params (merge) + persona + agent_params.
    r = await client.patch(
        f"/vpps/managed/{managed_id}",
        headers=auth,
        json={
            "params": {"pv_kw_peak": 9.0},
            "persona": "Updated brief.",
            "agent_params": {"demand_beta": 0.7},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == managed_id  # stable id across the re-provision
    assert body["params"]["pv_kw_peak"] == 9.0
    assert body["params"]["battery_kwh"] == 8.0  # merge preserved the untouched field

    # The updated definition is what a restart rehydrates (persona + params changed).
    fresh = Simulator(bus=InMemoryBus())
    await _rehydrate_managed_vpps(fresh)
    rehydrated = fresh.my_managed_vpps(user_id)[0]
    assert rehydrated.params.pv_kw_peak == 9.0
    assert rehydrated.agent.persona_prompt == "Updated brief."

    # A bad patch is rejected and must not strand the agent.
    r = await client.patch(
        f"/vpps/managed/{managed_id}", headers=auth, json={"agent_params": {"bogus": 1}}
    )
    assert r.status_code == 422, r.text
    r = await client.get("/vpps/managed", headers=auth)
    assert [v["id"] for v in r.json()] == [managed_id]

    # Patch on a non-existent managed agent → 404.
    r = await client.patch("/vpps/managed/999999", headers=auth, json={"persona": "x"})
    assert r.status_code == 404, r.text
