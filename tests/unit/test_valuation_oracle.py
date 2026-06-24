"""Unit tests for TruthfulValuationOracle.

The oracle is a verbatim extraction of the Truthful agent's economics, so these mirror
the truthful-agent cases at the signal level (fair prices, battery opportunity cost,
imbalance, SOC).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.valuation import TruthfulValuationOracle
from eflux.data.electricity_market import synthetic_quote
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _make_ctx(*, pv_kw: float, load_kw: float, soc_kwh: float = 5.0, markup_floor: float = 0.0) -> AgentContext:
    params = VPPParams(markup_floor=markup_floor)
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=soc_kwh, pv_kw=pv_kw, load_kw=load_kw)
    state.update_net()
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


def test_battery_opportunity_prices():
    sig = TruthfulValuationOracle(price_ref=Decimal("50.0")).estimate(_make_ctx(pv_kw=5.0, load_kw=1.0))
    assert sig.battery_sell_price == 50.0 / math.sqrt(0.9)
    assert sig.battery_buy_price == 50.0 * math.sqrt(0.9)
    # Neutral fair value is the midpoint of storage value and delivery cost.
    assert sig.marginal_battery_value == 0.5 * (sig.battery_sell_price + sig.battery_buy_price)


def test_pure_renewable_surplus_quotes_floor():
    sig = TruthfulValuationOracle(price_ref=Decimal("50.0")).estimate(
        _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    )
    assert sig.surplus_kwh == 4.0
    assert sig.deficit_kwh == 0.0
    assert sig.fair_sell_price == 0.1 * 50.0  # markup_floor * price_ref


def test_battery_sourced_surplus_quotes_delivery_cost():
    """When the net export exceeds current renewable output the surplus is battery-
    sourced → fair sell price = delivery cost. Synthesize it (unreachable from the
    stub DER physics alone) by forcing net_kw above pv+wind."""
    ctx = _make_ctx(pv_kw=2.0, load_kw=0.0)
    ctx.state.net_kw = 3.0  # > pv_kw + wind_kw (2.0)
    sig = TruthfulValuationOracle(price_ref=Decimal("50.0")).estimate(ctx)
    assert sig.fair_sell_price == 50.0 / math.sqrt(0.9)


def test_deficit_flat_bid_without_demand_beta():
    sig = TruthfulValuationOracle(price_ref=Decimal("50.0")).estimate(_make_ctx(pv_kw=0.5, load_kw=3.0))
    assert sig.deficit_kwh == 2.5
    assert sig.fair_buy_price == 50.0


def test_demand_beta_prices_scarcity_with_book_depth():
    oracle = TruthfulValuationOracle(price_ref=Decimal("50.0"), demand_beta=0.5)
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)  # this tick's sliver: -2.5 kWh
    ctx.open_orders_net_kwh = -5.0  # 5 kWh already resting unfilled
    sig = oracle.estimate(ctx)
    # total unserved 7.5 / 10 kWh battery → frac 0.75 → 50 * (1 + 0.5*0.75) = 68.75
    assert sig.fair_buy_price == 68.75


def test_demand_beta_capped_at_price_cap_mult():
    sig = TruthfulValuationOracle(price_ref=Decimal("50.0"), demand_beta=5.0).estimate(
        _make_ctx(pv_kw=0.5, load_kw=3.0)
    )
    assert sig.fair_buy_price == 75.0  # capped at 1.5 * 50


def test_external_market_quote_clamps_p2p_buy_and_sell_prices():
    oracle = TruthfulValuationOracle(price_ref=Decimal("50.0"), demand_beta=5.0)
    buy_ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)
    buy_ctx.market.external_market = synthetic_quote(
        price=Decimal("42"),
        status="real",
        source="CAISO OASIS RTM",
    )

    buy_sig = oracle.estimate(buy_ctx)

    assert buy_sig.fair_buy_price == 42.0
    assert buy_sig.battery_buy_price == 42.0

    sell_ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    sell_ctx.market.external_market = synthetic_quote(
        price=Decimal("42"),
        status="real",
        source="CAISO OASIS RTM",
    )

    sell_sig = oracle.estimate(sell_ctx)

    assert sell_sig.fair_sell_price == 42.0
    assert sell_sig.battery_sell_price == 50.0 / math.sqrt(0.9)


def test_soc_signal():
    sig = TruthfulValuationOracle().estimate(_make_ctx(pv_kw=2.0, load_kw=2.0, soc_kwh=3.0))
    assert sig.soc_frac == 0.3  # 3 / 10
    assert sig.soc_pressure == 0.3 - 0.5  # below the neutral mid → room to charge
