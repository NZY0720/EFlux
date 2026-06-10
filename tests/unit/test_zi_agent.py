"""Unit tests for the ZI (Zero-Intelligence) agent."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot, OrderIntent
from eflux.agents.zi import ZIAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import Battery, FlexibleLoad, PV


def _make_ctx(*, pv_kw: float, load_kw: float, soc_kwh: float = 5.0) -> AgentContext:
    params = VPPParams()
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=soc_kwh, pv_kw=pv_kw, load_kw=load_kw)
    state.update_net()
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
    agent = ZIAgent(price_ref=Decimal("50.0"), spread_frac=0.5)
    # Repeat a few times — random draws should all be in [25, 75]
    for _ in range(20):
        intents = ZIAgent(price_ref=Decimal("50.0"), spread_frac=0.5).decide(
            _make_ctx(pv_kw=5.0, load_kw=1.0)
        )
        if intents:
            assert Decimal("25.0") <= intents[0].price <= Decimal("75.0"), intents[0].price


def test_zi_min_qty_acts_as_floor_not_threshold():
    # The current implementation uses max(real, min_qty) — so a high min_qty
    # *forces* an order at exactly min_qty (it doesn't suppress it). Lock in
    # that behavior; change this if/when ZI is reworked.
    agent = ZIAgent(min_qty=Decimal("1000"))
    intents = agent.decide(_make_ctx(pv_kw=1.0, load_kw=0.5))
    assert len(intents) == 1
    assert intents[0].qty == Decimal("1000.0000")


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
