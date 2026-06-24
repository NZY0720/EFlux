"""Contract tests for the public story endpoints: agents roster, supply curve,
reflection feed, and the runtime speed control."""

from __future__ import annotations

import pytest

VALID_CATEGORIES = {"solar", "wind", "gas", "battery_load", "llm", "external"}


async def _login(client) -> dict[str, str]:
    r = await client.post("/auth/magic-link", json={"email": "story@hku.hk"})
    tok = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": tok})
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


@pytest.mark.asyncio
async def test_agents_roster_is_public_and_complete(client):
    r = await client.get("/market/agents")
    assert r.status_code == 200, r.text
    agents = r.json()
    assert len(agents) == 42  # 36 declared roster entries + 6 auto-spawned PPO mirrors

    by_cat: dict[str, int] = {}
    for a in agents:
        assert a["category"] in VALID_CATEGORIES
        by_cat[a["category"]] = by_cat.get(a["category"], 0) + 1
        assert 0.0 <= a["soc_frac"] <= 1.0
        float(a["pnl"])  # parseable decimal string
    # The default roster spans the whole merit order.
    assert by_cat.get("gas") == 2
    assert by_cat.get("wind") == 8
    assert by_cat.get("llm") == 6

    llm = next(a for a in agents if a["is_llm"])
    assert llm["name"] == "my-llm-vpp"
    assert llm["llm_health_state"] in ("live", "degraded", "offline")


@pytest.mark.asyncio
async def test_supply_curve_orders_sorted_best_first(client):
    r = await client.get("/market/supply_curve")
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data.keys()) == {"sim_ts", "asks", "bids"}
    ask_prices = [float(o["price"]) for o in data["asks"]]
    bid_prices = [float(o["price"]) for o in data["bids"]]
    assert ask_prices == sorted(ask_prices)
    assert bid_prices == sorted(bid_prices, reverse=True)
    for o in data["asks"] + data["bids"]:
        assert o["category"] in VALID_CATEGORIES
        assert float(o["qty"]) > 0


@pytest.mark.asyncio
async def test_reflections_feed_is_public(client):
    r = await client.get("/market/reflections?limit=5")
    assert r.status_code == 200, r.text
    entries = r.json()
    assert isinstance(entries, list)
    for e in entries:  # empty until the first reflection interval elapses
        assert e["vpp_name"] == "my-llm-vpp"
        assert e["health_state"] in ("live", "degraded", "offline")


@pytest.mark.asyncio
async def test_speed_control_requires_auth_and_gates_external_orders(client):
    r = await client.post("/market/speed", json={"speed": 10.0})
    assert r.status_code == 401

    headers = await _login(client)

    r = await client.post("/market/speed", headers=headers, json={"speed": 2.5})
    assert r.status_code == 422

    r = await client.post("/vpps", headers=headers, json={"name": "speed-ui-vpp", "params": {}})
    vpp_id = r.json()["id"]
    order = {"vpp_id": vpp_id, "side": "buy", "price": "80", "qty": "0.05"}

    r = await client.post("/market/speed", headers=headers, json={"speed": 10.0})
    assert r.status_code == 200 and r.json()["speed"] == 10.0
    r = await client.post("/orders", headers=headers, json=order)
    assert r.status_code == 409, r.text

    r = await client.post("/market/speed", headers=headers, json={"speed": 1.0})
    assert r.status_code == 200 and r.json()["is_realtime"] is True
    r = await client.post("/orders", headers=headers, json=order)
    assert r.status_code == 200, r.text

    snap = (await client.get("/market/snapshot")).json()
    assert snap["speed"] == 1.0
    # Balance KPI rides the snapshot: live aggregates from the 30-VPP roster.
    balance = snap["balance"]
    assert balance["gas_capacity_kw"] > 0
    assert balance["supply_demand_ratio"] is None or balance["supply_demand_ratio"] > 0
