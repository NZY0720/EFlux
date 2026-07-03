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
    assert body["default"] == "hybrid"
    algorithms = {entry["id"]: entry for entry in body["algorithms"]}
    assert list(algorithms) == ["hybrid", "ppo", "truthful", "zi", "zip", "gd", "aa"]
    assert algorithms["hybrid"]["uses_llm"] is True
    assert algorithms["hybrid"]["supports_online_learning"] is True
    assert algorithms["ppo"]["uses_llm"] is False
    assert algorithms["ppo"]["supports_online_learning"] is True
    assert {p["name"] for p in algorithms["zip"]["params"]} == {
        "beta",
        "momentum",
        "rel_perturb",
        "abs_perturb",
        "init_margin",
        "max_margin",
    }
    assert algorithms["gd"]["params"] == []


async def test_non_hybrid_rejects_persona_and_guidance(client):
    auth = await _login(client, "zip-owner@hku.hk")
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={
            "name": "zi-with-persona",
            "algorithm": "zi",
            "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0},
            "persona": "should fail",
        },
    )
    assert r.status_code == 422, r.text

    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={
            "name": "zip-managed",
            "algorithm": "zip",
            "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0},
            "agent_params": {"beta": 0.2},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["algorithm"] == "zip"
    managed_id = body["id"]

    r = await client.put(
        f"/vpps/managed/{managed_id}/guidance",
        headers=auth,
        json={"risk_budget": 0.4},
    )
    assert r.status_code == 409, r.text
