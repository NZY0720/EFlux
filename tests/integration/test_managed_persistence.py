"""Managed agents (Tier 0) persist across a simulated restart and can be deleted.

The live market state is ephemeral, but a provisioned managed agent's *definition* is
stored (vpps.is_managed rows) and re-provisioned on startup — see
docs/EXTERNAL_PARTICIPATION.md and api/main._rehydrate_managed_vpps.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from eflux.agents.hybrid import HybridPolicyAgent
from eflux.agents.reflective.pool import SharedLLM
from eflux.agents.zip_agent import ZIPAgent
from eflux.api.main import _rehydrate_managed_vpps
from eflux.api.routers import vpps as vpps_router
from eflux.bridge import InMemoryBus
from eflux.db.models import VPP
from eflux.simulator.runner import Simulator


async def _login(client) -> tuple[str, int]:
    r = await client.post("/auth/magic-link", json={"email": "persist@hku.hk"})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    body = r.json()
    return body["session_token"], body["user_id"]


class _FakeLLMClient:
    def __init__(self, model: str = "deepseek-v4-flash") -> None:
        self.model = model

    async def chat(self, messages, *, temperature=0.2, max_tokens=None):
        del messages, temperature, max_tokens
        return "{}"


def _fake_shared_llm() -> SharedLLM:
    client = _FakeLLMClient()
    return SharedLLM(
        client=client,
        status="live fake",
        strategy_suffix=f"fake:{client.model}",
        base_url="http://fake",
        api_key="fake",
        default_model=client.model,
    )


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
async def test_zip_managed_agent_persists_algorithm_across_restart(client):
    sess, user_id = await _login(client)
    auth = {"Authorization": f"Bearer {sess}"}

    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={
            "name": "persistent-zip",
            "algorithm": "zip",
            "llm_enabled": False,
            "params": {"pv_kw_peak": 3.0, "battery_kwh": 8.0},
            "agent_params": {"beta": 0.25},
        },
    )
    assert r.status_code == 201, r.text
    managed_id = r.json()["id"]

    fresh = Simulator(bus=InMemoryBus())
    await _rehydrate_managed_vpps(fresh)
    mine = fresh.my_managed_vpps(user_id)
    assert [v.name for v in mine] == ["persistent-zip"]
    assert mine[0].managed_def_id == managed_id
    assert mine[0].algorithm == "zip"
    assert mine[0].llm_enabled is False
    assert isinstance(mine[0].agent, ZIPAgent)


@pytest.mark.asyncio
async def test_legacy_managed_config_without_algorithm_rehydrates_as_llm_ppo(client, db_session):
    sess, user_id = await _login(client)
    auth = {"Authorization": f"Bearer {sess}"}
    await client.get("/vpps/managed", headers=auth)

    row = VPP(
        owner_id=user_id,
        name="legacy-hybrid",
        params={"pv_kw_peak": 2.0, "battery_kwh": 5.0},
        is_external=True,
        is_managed=True,
        managed_config={
            "persona": None,
            "agent_params": {"demand_beta": 0.2},
            "seed": 12,
            "model": None,
        },
    )
    db_session.add(row)
    await db_session.commit()

    fresh = Simulator(bus=InMemoryBus())
    await _rehydrate_managed_vpps(fresh)
    mine = fresh.my_managed_vpps(user_id)
    assert [v.name for v in mine] == ["legacy-hybrid"]
    # A pre-split row with no algorithm key was the LLM+PPO hybrid → base ppo with the LLM on.
    assert mine[0].algorithm == "ppo"
    assert mine[0].llm_enabled is True
    assert isinstance(mine[0].agent, HybridPolicyAgent)


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


@pytest.mark.asyncio
async def test_rehydrate_retired_model_falls_back_to_default(client, db_session):
    sess, user_id = await _login(client)
    auth = {"Authorization": f"Bearer {sess}"}
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={"name": "retired-model", "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0}},
    )
    assert r.status_code == 201, r.text
    managed_id = r.json()["id"]

    row = await db_session.get(VPP, managed_id)
    row.managed_config = {**dict(row.managed_config or {}), "model": "retired-model-id"}
    await db_session.commit()

    fresh = Simulator(bus=InMemoryBus())
    fresh.shared_llm = _fake_shared_llm()
    await _rehydrate_managed_vpps(fresh)

    vpp = fresh.my_managed_vpps(user_id)[0]
    assert vpp.agent.strategist.client.model == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_rehydrate_scrubs_malformed_external_guidance(client, db_session):
    sess, user_id = await _login(client)
    auth = {"Authorization": f"Bearer {sess}"}
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={"name": "bad-guidance", "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0}},
    )
    assert r.status_code == 201, r.text
    managed_id = r.json()["id"]

    row = (await db_session.execute(select(VPP).where(VPP.id == managed_id))).scalar_one()
    row.managed_config = {
        **dict(row.managed_config or {}),
        "guidance_mode": "external",
        "external_guidance": {"risk_budget": "not-a-number"},
    }
    await db_session.commit()

    fresh = Simulator(bus=InMemoryBus())
    fresh.shared_llm = _fake_shared_llm()
    await _rehydrate_managed_vpps(fresh)

    vpp = fresh.my_managed_vpps(user_id)[0]
    assert vpps_router._guidance_source(vpp) == "platform"
    await db_session.refresh(row)
    assert row.managed_config["guidance_mode"] == "platform"
    assert "external_guidance" not in row.managed_config


@pytest.mark.asyncio
async def test_managed_name_reusable_after_delete(client):
    sess, _ = await _login(client)
    auth = {"Authorization": f"Bearer {sess}"}
    body = {"name": "reuse-me", "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0}}

    r = await client.post("/vpps/managed", headers=auth, json=body)
    assert r.status_code == 201, r.text
    mid = r.json()["id"]

    # A live duplicate is rejected...
    r = await client.post("/vpps/managed", headers=auth, json=body)
    assert r.status_code == 409, r.text

    # ...but once deleted, the name is reusable again.
    r = await client.delete(f"/vpps/managed/{mid}", headers=auth)
    assert r.status_code == 204, r.text
    r = await client.post("/vpps/managed", headers=auth, json=body)
    assert r.status_code == 201, r.text
