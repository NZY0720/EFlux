"""Unit tests for the ZI (Zero-Intelligence) agent."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot, OrderIntent
from eflux.agents.zi import ZIAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _make_ctx(*, pv_kw: float, load_kw: float, soc_kwh: float = 5.0) -> AgentContext:
    params = VPPParams()
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=soc_kwh, pv_kw=pv_kw, load_kw=load_kw)
    state.update_net()
    # Agents quote from the accumulated untraded balance (maintained by the
    # runner). With tick_duration_h=1.0 one tick's accumulation equals net_kw.
    state.pending_net_kwh = state.net_kw * 1.0
    market = MarketSnapshot(
        sim_ts=state.sim_ts,
        best_bid=Decimal("48"),
        best_ask=Decimal("52"),
        last_price=Decimal("50"),
        mid_price=Decimal("50"),
    )
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(kw_peak=params.pv_kw_peak),
        battery=Battery(capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max, soc_kwh=soc_kwh),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=market,
        rng=random.Random(42),
        tick_duration_h=1.0,
    )


def test_zi_outputs_sell_when_pv_exceeds_load():
    agent = ZIAgent()
    intents = agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))
    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert intents[0].qty > 0


def test_zi_outputs_buy_when_load_exceeds_pv():
    agent = ZIAgent()
    intents = agent.decide(_make_ctx(pv_kw=0.5, load_kw=4.0))
    assert len(intents) == 1
    assert intents[0].side == "buy"
    assert intents[0].qty > 0


def test_zi_price_within_rational_range():
    # Repeat a few times — random draws should all be in [25, 75]
    for _ in range(20):
        intents = ZIAgent(price_ref=Decimal("50.0"), spread_frac=0.5).decide(
            _make_ctx(pv_kw=5.0, load_kw=1.0)
        )
        if intents:
            assert Decimal("25.0") <= intents[0].price <= Decimal("75.0"), intents[0].price


def test_zi_min_qty_acts_as_threshold():
    # min_qty is a quoting threshold: below it the agent stays quiet and lets
    # the runner keep accumulating (it must NOT pad the order up to min_qty,
    # which would conjure energy out of nothing).
    agent = ZIAgent(min_qty=Decimal("1000"))
    intents = agent.decide(_make_ctx(pv_kw=1.0, load_kw=0.5))
    assert intents == []


def test_zi_quotes_accumulated_balance_not_per_tick_sliver():
    # A 1-second tick credits ~1e-3 kWh per tick; the agent should quote once
    # the accumulated balance clears min_qty, sized to the balance.
    agent = ZIAgent()
    ctx = _make_ctx(pv_kw=0.0, load_kw=2.5)
    tick_h = 1.0 / 3600.0
    ctx.tick_duration_h = tick_h
    ctx.state.pending_net_kwh = 0.0
    fired_at = None
    for tick in range(1, 31):
        ctx.state.pending_net_kwh += ctx.state.net_kw * tick_h
        intents = agent.decide(ctx)
        if intents:
            fired_at = tick
            break
    assert fired_at is not None, "ZI should quote within 30 one-second ticks at 2.5 kW deficit"
    assert intents[0].side == "buy"
    # qty reflects the accumulated deficit (plus small battery headroom term).
    assert intents[0].qty >= Decimal("0.01")


def test_zi_returns_empty_when_perfectly_balanced():
    agent = ZIAgent()
    # pv == load and battery_kw == 0 → net_kwh == 0 → no order
    intents = agent.decide(_make_ctx(pv_kw=2.0, load_kw=2.0))
    # ZI's branch on net_kwh == 0 returns [].
    assert intents == []


def test_order_intent_dataclass_shape():
    intent = OrderIntent(side="buy", price=Decimal("50"), qty=Decimal("1"))
    assert intent.side == "buy"
    assert intent.price == Decimal("50")
    assert intent.qty == Decimal("1")
