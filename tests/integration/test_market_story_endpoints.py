"""Contract tests for the public story endpoints: agents roster, supply curve,
reflection feed, and the runtime speed control."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
        assert a["trade_count"] >= a["recent_trade_count"]
    # The default roster spans the whole merit order.
    assert by_cat.get("gas") == 2
    assert by_cat.get("wind") == 8
    assert by_cat.get("llm") == 6

    llm = next(a for a in agents if a["is_llm"])
    assert llm["name"] == "my-llm-vpp"
    assert llm["llm_health_state"] in ("live", "degraded", "offline")
    assert "llm_model" in llm  # arena display field (None when the LLM is unconfigured)
    assert all(a["llm_model"] is None for a in agents if not a["is_llm"])
    mirrors = [a for a in agents if a["mirror_of"] is not None]
    assert len(mirrors) == 6
    assert all(a["name"] == f"{a['mirror_of']}-ppo-mirror" for a in mirrors)


@pytest.mark.asyncio
async def test_agent_summary_separates_archetype_from_battery_solar_resources(client):
    from eflux.agents.character import derive_character
    from eflux.vpp.base import VPPParams

    agents = (await client.get("/market/agents")).json()
    fixture = next(agent for agent in agents if agent["battery_kwh"] > 0 and agent["pv_kw_peak"] > 0)
    params = VPPParams(
        pv_kw_peak=fixture["pv_kw_peak"],
        wind_kw_rated=fixture["wind_kw_rated"],
        battery_kwh=fixture["battery_kwh"],
        battery_kw_max=fixture["battery_kw_max"],
        load_kw_base=fixture["load_kw_base"],
        gas_kw_max=fixture["gas_kw_max"],
        gas_cost_per_mwh=fixture["gas_cost_per_mwh"],
    )

    assert fixture["archetype"] == derive_character(params).archetype
    assert "solar" in fixture["resources"]
    assert "battery" in fixture["resources"]


@pytest.mark.asyncio
async def test_arena_payload_exposes_evidence_for_client_threshold_gate(db_session):
    from httpx import ASGITransport, AsyncClient

    from eflux.api.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        sim = app.state.simulator
        vpp = next(vpp for vpp in sim.vpps.values() if vpp.is_my_vpp)
        now = sim.clock.now_sim()
        vpp.trade_count = 9
        vpp.observed_since_sim = now - timedelta(minutes=29)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            below = (await ac.get("/market/arena")).json()
            vpp.trade_count = 10
            vpp.observed_since_sim = now - timedelta(minutes=30)
            above = (await ac.get("/market/arena")).json()

    assert below["min_trades"] == 10
    assert below["min_observation_min"] == 30
    below_agent = next(agent for agent in below["agents"] if agent["id"] == vpp.vpp_id)
    assert below_agent["trade_count"] == 9
    assert 28.9 <= below_agent["observation_min"] < 30
    above_agent = next(agent for agent in above["agents"] if agent["id"] == vpp.vpp_id)
    assert above_agent["trade_count"] == 10
    assert above_agent["observation_min"] >= 30


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
async def test_reflections_feed_serializes_meta_control(db_session):
    from httpx import ASGITransport, AsyncClient

    from eflux.api.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        sim = app.state.simulator
        vpp = sim.my_managed_vpps()[0]
        ts = datetime.now(UTC)
        vpp.agent.strategist = SimpleNamespace(
            client=object(),
            ok_count=1,
            fail_count=0,
            last_ok_ts=ts,
            reflection_log=[
                {
                    "ts": ts,
                    "ok": True,
                    "preferred_modes": ["battery_arbitrage"],
                    "avoid_modes": [],
                    "risk_budget": 0.8,
                    "soc_target": 0.6,
                    "execution_style": "Charge before high grid prices.",
                    "rationale": "Charge before high grid prices.",
                    "lesson": "Grid timing beats book chasing here.",
                    "meta_control": {"w_soc_mult": 1.4, "mode_reg_coef": 0.25},
                    "error": None,
                }
            ],
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/market/reflections?limit=1")

    assert r.status_code == 200, r.text
    entry = r.json()[0]
    assert entry["meta_control"]["w_soc_mult"] == 1.4
    assert entry["meta_control"]["mode_reg_coef"] == 0.25


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
