from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.character import (
    NEUTRAL_CHARACTER,
    Character,
    derive_character,
    endowment_resources,
    endowment_summary,
)
from eflux.agents.hybrid.agent import StrategyAgent
from eflux.agents.llm.strategist import build_strategist_user_message
from eflux.agents.strategy.schema import StrategyAction, StrategyMode
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _params(**kw) -> VPPParams:
    base = dict(pv_kw_peak=0.0, battery_kwh=10.0, battery_kw_max=3.0, load_kw_base=0.0)
    base.update(kw)
    return VPPParams(**base)


def test_derive_character_archetypes():
    assert derive_character(_params(gas_kw_max=20.0)).archetype == "dispatchable"
    assert derive_character(_params(battery_kwh=60.0, load_kw_base=11.0)).archetype == "arbitrageur"
    assert derive_character(_params(pv_kw_peak=9.0, load_kw_base=0.0)).archetype == "producer"
    assert (
        derive_character(_params(pv_kw_peak=0.0, load_kw_base=6.0, battery_kwh=8.0)).archetype
        == "consumer"
    )
    # roughly balanced endowment → neutral identity
    assert derive_character(_params(pv_kw_peak=4.0, load_kw_base=3.5, battery_kwh=8.0)).is_neutral()


def test_neutral_character_is_strict_identity():
    action = StrategyAction(
        mode=StrategyMode.COVER_DEFICIT, aggressiveness=0.4, qty_fraction=0.8, soc_target=0.55
    )
    assert NEUTRAL_CHARACTER.apply(action) is action
    assert Character().is_neutral()


def test_character_modulates_and_differentiates():
    action = StrategyAction(
        mode=StrategyMode.COVER_DEFICIT, aggressiveness=0.6, qty_fraction=0.8, soc_target=0.5
    )
    consumer = derive_character(_params(pv_kw_peak=0.0, load_kw_base=6.0, battery_kwh=8.0))
    arb = derive_character(_params(battery_kwh=60.0, load_kw_base=11.0))
    c_out = consumer.apply(action)
    a_out = arb.apply(action)
    # cautious consumer: smaller size, keeps charge in reserve (higher SOC target)
    assert c_out.qty_fraction < action.qty_fraction
    assert c_out.soc_target > action.soc_target
    # arbitrageur presses harder (size scaled up, capped at 1.0)
    assert a_out.qty_fraction >= action.qty_fraction
    # different endowments → different behaviour
    assert c_out != a_out


def test_public_views_json_serializable():
    ch = derive_character(_params(battery_kwh=60.0, load_kw_base=11.0))
    json.dumps(ch.to_public())
    summary = endowment_summary(_params(battery_kwh=60.0, pv_kw_peak=2.0, load_kw_base=11.0))
    json.dumps(summary)
    assert summary["battery_kwh"] == 60.0


def test_resources_list_assets_without_changing_behavioural_archetype():
    # A load-heavy factory may own a little PV. Its resources should say so,
    # while its archetype remains consumer rather than being called Solar.
    params = _params(pv_kw_peak=1.0, battery_kwh=0.0, load_kw_base=10.0)
    assert derive_character(params).archetype == "consumer"
    assert endowment_resources(params) == ["solar", "load"]


def test_strategist_message_carries_endowment_and_character():
    base = build_strategist_user_message(
        recent_pnl=[], soc_frac=0.5, best_bid=48.0, best_ask=52.0, last_price=50.0
    )
    enriched = build_strategist_user_message(
        recent_pnl=[],
        soc_frac=0.5,
        best_bid=48.0,
        best_ask=52.0,
        last_price=50.0,
        endowment={"battery_kwh": 30.0},
        character={"archetype": "arbitrageur"},
    )
    assert "endowment" not in json.loads(base)
    data = json.loads(enriched)
    assert data["endowment"]["battery_kwh"] == 30.0
    assert data["character"]["archetype"] == "arbitrageur"


def _deficit_ctx(params: VPPParams) -> AgentContext:
    state = VPPState(
        sim_ts=datetime(2026, 1, 1, 12, tzinfo=UTC),
        soc_kwh=params.battery_kwh * 0.5,
        pv_kw=0.0,
        load_kw=5.0,
    )
    state.update_net()
    state.pending_net_kwh = state.net_kw
    market = MarketSnapshot(
        sim_ts=state.sim_ts,
        best_bid=Decimal("48"),
        best_ask=Decimal("52"),
        last_price=Decimal("50"),
        mid_price=Decimal("50"),
        market_mode="realprice",
    )
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(kw_peak=params.pv_kw_peak),
        battery=Battery(
            capacity_kwh=params.battery_kwh,
            max_power_kw=params.battery_kw_max,
            soc_kwh=params.battery_kwh * 0.5,
        ),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=market,
        rng=random.Random(0),
        tick_duration_h=1.0,
    )


def test_strategy_agent_character_changes_orders():
    params = _params(pv_kw_peak=0.0, load_kw_base=6.0, battery_kwh=8.0, battery_kw_max=3.0)
    consumer = derive_character(params)
    assert not consumer.is_neutral()

    neutral_orders = StrategyAgent(price_ref=Decimal("50")).decide(_deficit_ctx(params)).orders
    consumer_orders = (
        StrategyAgent(price_ref=Decimal("50"), character=consumer)
        .decide(_deficit_ctx(params))
        .orders
    )

    assert neutral_orders and consumer_orders
    n_qty = sum(float(o.qty_kwh) for o in neutral_orders)
    c_qty = sum(float(o.qty_kwh) for o in consumer_orders)
    # the cautious consumer character scales the covered quantity down
    assert c_qty < n_qty
