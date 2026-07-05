from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _login(client, email="algorithms@hku.hk") -> dict:
    r = await client.post("/auth/magic-link", json={"email": email})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


async def test_algorithms_roster_shape(client):
    auth = await _login(client)
    r = await client.get("/vpps/algorithms", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    # The picker now offers *base* algorithms; the LLM is a separate toggle.
    assert body["default"] == "ppo"
    assert body["default_llm_enabled"] is True
    algorithms = {entry["id"]: entry for entry in body["algorithms"]}
    assert list(algorithms) == ["ppo", "truthful", "zip", "gd", "aa"]
    # Every base can be paired with the LLM strategist.
    assert all(a["llm_capable"] is True for a in algorithms.values())
    assert algorithms["ppo"]["supports_online_learning"] is True
    assert algorithms["truthful"]["supports_online_learning"] is False
    assert {p["name"] for p in algorithms["zip"]["params"]} == {
        "beta",
        "momentum",
        "rel_perturb",
        "abs_perturb",
        "init_margin",
        "max_margin",
    }
    assert algorithms["gd"]["params"] == []


async def test_zi_is_removed(client):
    auth = await _login(client, "zi-gone@hku.hk")
    r = await client.get("/vpps/algorithms", headers=auth)
    assert "zi" not in {e["id"] for e in r.json()["algorithms"]}
    # Provisioning a ZI managed agent is rejected — it is no longer a valid base algorithm.
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={"name": "zi-agent", "algorithm": "zi", "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0}},
    )
    assert r.status_code == 422, r.text


async def test_non_llm_rejects_persona_and_guidance(client):
    auth = await _login(client, "zip-owner@hku.hk")
    # persona/model require the LLM strategist to be enabled.
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={
            "name": "aa-with-persona",
            "algorithm": "aa",
            "llm_enabled": False,
            "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0},
            "persona": "should fail",
        },
    )
    assert r.status_code == 422, r.text

    # A plain (no-LLM) baseline provisions fine but cannot be externally steered (Tier A3).
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={
            "name": "zip-managed",
            "algorithm": "zip",
            "llm_enabled": False,
            "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0},
            "agent_params": {"beta": 0.2},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["algorithm"] == "zip"
    assert body["llm_enabled"] is False

    r = await client.put(
        f"/vpps/managed/{body['id']}/guidance",
        headers=auth,
        json={"risk_budget": 0.4},
    )
    assert r.status_code == 409, r.text


async def test_llm_plus_baseline_supports_persona_and_guidance(client):
    """LLM + a classical baseline (e.g. AA) is a first-class combination: it accepts a persona
    and, because it runs the HybridPolicyAgent stack, external guidance (Tier A3) too."""
    auth = await _login(client, "llm-aa@hku.hk")
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={
            "name": "llm-aa",
            "algorithm": "aa",
            "llm_enabled": True,
            "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0},
            "agent_params": {"pstar_alpha": 0.3},
            "persona": "capture spreads",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["algorithm"] == "aa"
    assert body["llm_enabled"] is True

    r = await client.put(
        f"/vpps/managed/{body['id']}/guidance",
        headers=auth,
        json={"risk_budget": 0.4},
    )
    assert r.status_code == 200, r.text
