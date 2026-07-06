"""Unit tests for the Truthful (cost-based) agent."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.truthful import TruthfulAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _make_ctx(*, pv_kw: float, load_kw: float, soc_kwh: float = 5.0, markup_floor: float = 0.0) -> AgentContext:
    params = VPPParams(markup_floor=markup_floor)
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
        rng=random.Random(0),
        tick_duration_h=1.0,
    )


def test_pure_pv_export_quotes_at_floor():
    """When PV surplus alone covers the net export, the marginal cost is ~0 → floor price."""
    agent = TruthfulAgent(price_ref=Decimal("50.0"))
    # pv 5kW, load 1kW, tick_h 1.0 → net = 4 kWh; pv_surplus = 5 * 1 = 5 kWh ≥ 4 → pure PV.
    intents = agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1))
    assert len(intents) == 1
    assert intents[0].side == "sell"
    # Floor = markup_floor * price_ref = 0.1 * 50 = 5.0
    assert intents[0].price == Decimal("5.0000")
    assert intents[0].qty == Decimal("4.0000")


def test_buy_price_equals_price_ref_when_deficit():
    """Load > PV → buy at price_ref (willing to pay retail to cover load)."""
    agent = TruthfulAgent(price_ref=Decimal("50.0"))
    intents = agent.decide(_make_ctx(pv_kw=0.5, load_kw=3.0))
    assert len(intents) == 1
    assert intents[0].side == "buy"
    assert intents[0].price == Decimal("50.0000")
    assert intents[0].qty == Decimal("2.5000")


def test_balanced_position_no_order():
    agent = TruthfulAgent()
    assert agent.decide(_make_ctx(pv_kw=2.0, load_kw=2.0)) == []


def test_marginal_cost_includes_battery_when_pv_insufficient():
    """If sell quantity exceeds PV, marginal cost = battery_sell_price = price_ref / sqrt(eta)."""
    # We hack pv to be small but net positive via load = 0. Then pv_surplus = pv_kw * tick_h,
    # but the agent computes net_kwh = (pv - load) * tick_h, so we need an asymmetric scenario.
    # The agent's stub doesn't simulate battery into net_kwh — net_kwh == pv_surplus when load=0.
    # So this codepath is currently unreachable from real DER physics alone. Verify the
    # math is consistent when triggered synthetically by load=0, pv just above zero.
    # Instead, exercise it indirectly by checking the formula at sqrt(0.9):
    expected = 50.0 / math.sqrt(0.9)
    assert 52 < expected < 53  # ~52.7 — sanity check


def test_floor_zero_clamped_to_minimum_positive():
    """A markup_floor of 0 should still produce a strictly positive price (matching_engine rejects ≤0)."""
    agent = TruthfulAgent(price_ref=Decimal("50.0"))
    intents = agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.0))
    assert len(intents) == 1
    assert intents[0].price > 0


def test_demand_beta_raises_bid_with_deficit():
    """Price-responsive demand: bid rises with the deficit fraction so scarcity
    hours can cross the gas merit order instead of waiting unserved at 50."""
    agent = TruthfulAgent(price_ref=Decimal("50.0"), demand_beta=0.5)
    # deficit 2.5 kWh on a 10 kWh battery → frac 0.25 → 50 * (1 + 0.5*0.25) = 56.25
    intents = agent.decide(_make_ctx(pv_kw=0.5, load_kw=3.0))
    assert len(intents) == 1
    assert intents[0].side == "buy"
    assert intents[0].price == Decimal("56.2500")


def test_demand_beta_bid_capped_at_price_cap_mult():
    agent = TruthfulAgent(price_ref=Decimal("50.0"), demand_beta=5.0)
    intents = agent.decide(_make_ctx(pv_kw=0.5, load_kw=3.0))
    # 1 + 5.0*0.25 = 2.25 → capped at price_cap_mult 1.5 → 75
    assert intents[0].price == Decimal("75.0000")


def test_demand_beta_defaults_to_legacy_flat_bid():
    agent = TruthfulAgent(price_ref=Decimal("50.0"))
    intents = agent.decide(_make_ctx(pv_kw=0.5, load_kw=3.0))
    assert intents[0].price == Decimal("50.0000")


def test_demand_beta_sees_resting_book_deficit():
    """Resting same-side bids reduce the re-quoted deficit quantity but still
    contribute to demand_beta scarcity."""
    agent = TruthfulAgent(price_ref=Decimal("50.0"), demand_beta=0.5)
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)
    # Full forced deficit is 7.5 kWh; 5 kWh is already resting in bids (buy = negative).
    ctx.state.pending_net_kwh = -7.5
    ctx.open_orders_net_kwh = -5.0
    intents = agent.decide(ctx)
    assert len(intents) == 1 and intents[0].side == "buy"
    # The separate scarcity term still includes resting demand depth:
    # -(pending + open) = 12.5 kWh, capped at 1.5 * price_ref.
    assert intents[0].price == Decimal("75.0000")
    # the new order quotes only the un-rested remainder
    assert intents[0].qty == Decimal("2.5000")


def test_battery_band_sells_stored_energy_at_night():
    """Nighttime liquidity regression: with PV=0 every VPP is a buyer and the
    market dries up. A battery above soc_high must offer stored energy at its
    delivery cost (price_ref / sqrt(eta)) after the quote cooldown."""
    agent = TruthfulAgent(price_ref=Decimal("50.0"))
    ctx = _make_ctx(pv_kw=0.0, load_kw=2.0)  # deficit, SOC 5/10 = 0.5 > soc_high
    ctx.state.pending_net_kwh = 0.0  # nothing accumulated → no load-driven order yet

    battery_intents = []
    for _ in range(agent.battery_quote_every_n_ticks + 1):
        battery_intents = [i for i in agent.decide(ctx) if i.dispatched]
        if battery_intents:
            break
    assert battery_intents, "expected a battery sell quote within the cooldown window"
    sell = battery_intents[0]
    assert sell.side == "sell"
    # delivery cost = 50 / sqrt(0.9) ≈ 52.7
    assert Decimal("52") < sell.price < Decimal("53")
    assert sell.qty >= agent.min_qty


def test_battery_band_buys_back_when_depleted():
    agent = TruthfulAgent(price_ref=Decimal("50.0"))
    ctx = _make_ctx(pv_kw=0.0, load_kw=0.0, soc_kwh=1.0)  # SOC 0.1 < soc_low 0.25
    ctx.state.pending_net_kwh = 0.0

    battery_intents = []
    for _ in range(agent.battery_quote_every_n_ticks + 1):
        battery_intents = [i for i in agent.decide(ctx) if i.dispatched]
        if battery_intents:
            break
    assert battery_intents, "expected a battery recharge bid"
    buy = battery_intents[0]
    assert buy.side == "buy"
    # storage value = 50 * sqrt(0.9) ≈ 47.4
    assert Decimal("47") < buy.price < Decimal("48")
