from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.gas import GasGeneratorAgent
from eflux.agents.strategy.compiler import OrderProgramCompiler
from eflux.agents.strategy.schema import StrategyAction, StrategyMode
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.valuation import TruthfulValuationOracle
from eflux.agents.zip_agent import ZIPAgent
from eflux.market.delivery import OrderPurpose
from eflux.market.products import DeliveryInterval
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _interval() -> DeliveryInterval:
    start = NOW + timedelta(minutes=5)
    return DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )


def _ctx(
    *,
    projected_net_kwh: float,
    soc_kwh: float = 4.0,
    battery_kw: float = 3.0,
    params: VPPParams | None = None,
) -> AgentContext:
    params = params or VPPParams(battery_kw_max=battery_kw)
    state = VPPState(
        sim_ts=NOW,
        soc_kwh=soc_kwh,
        pv_kw=max(0.0, projected_net_kwh * 12.0),
        load_kw=max(0.0, -projected_net_kwh * 12.0),
    )
    state.update_net()
    market = MarketSnapshot(
        sim_ts=NOW,
        best_bid=Decimal("45"),
        best_ask=Decimal("55"),
        last_price=Decimal("50"),
        mid_price=Decimal("50"),
    )
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(params.pv_kw_peak),
        battery=Battery(
            params.battery_kwh,
            params.battery_kw_max,
            params.battery_eta_rt,
            soc_kwh,
        ),
        load=FlexibleLoad(params.load_kw_base),
        market=market,
        rng=random.Random(0),
        tick_duration_h=1 / 3600,
        delivery_intervals=(_interval(),),
        decision_interval_sec=30,
        projected_net_kwh=projected_net_kwh,
    )


def test_truthful_balance_order_targets_explicit_product_and_purpose():
    decision = TruthfulAgent().decide(_ctx(projected_net_kwh=0.5))
    balance = [order for order in decision.orders if order.purpose == OrderPurpose.BALANCE]
    assert len(balance) == 1
    assert balance[0].side == "sell"
    assert balance[0].qty_kwh == Decimal("0.5000")
    assert balance[0].interval == _interval()
    assert balance[0].ttl_sec == 30


def test_truthful_battery_quantity_is_terminal_power_and_efficiency_correct():
    ctx = _ctx(projected_net_kwh=0.0, soc_kwh=5.0, battery_kw=10.0)
    decision = TruthfulAgent(soc_high=0.45).decide(ctx)
    battery = [order for order in decision.orders if order.purpose == OrderPurpose.BATTERY]
    assert len(battery) == 1
    expected_terminal = (5.0 - 4.5) * math.sqrt(ctx.battery.eta_rt)
    assert float(battery[0].qty_kwh) == pytest.approx(expected_terminal, abs=5e-5)


def test_gas_agent_internalizes_startup_cost_in_first_interval_offer():
    params = VPPParams(
        pv_kw_peak=0,
        battery_kwh=0,
        battery_kw_max=0,
        load_kw_base=0,
        gas_kw_max=12,
        gas_cost_per_mwh=60,
        gas_startup_cost_usd=0.25,
    )
    decision = GasGeneratorAgent().decide(_ctx(projected_net_kwh=0.0, soc_kwh=0, params=params))
    order = decision.orders[0]
    assert order.purpose == OrderPurpose.DISPATCHABLE
    assert order.qty_kwh == Decimal("1.0000")
    assert order.price == Decimal("310.0000")


def test_classical_baseline_also_emits_canonical_agent_decision():
    decision = ZIPAgent().decide(_ctx(projected_net_kwh=-0.5))
    assert decision.orders[0].side == "buy"
    assert decision.orders[0].purpose == OrderPurpose.BALANCE


def test_compiler_lowers_cancel_reprice_to_atomic_replace_request():
    ctx = _ctx(projected_net_kwh=0.5)
    from eflux.agents.base import OpenOrderView

    ctx.open_orders = [
        OpenOrderView(
            order_id=7,
            side="sell",
            price=Decimal("60"),
            remaining_qty=Decimal("0.5"),
            age_ticks=3,
        )
    ]
    valuation = TruthfulValuationOracle().estimate(ctx)
    compiled = OrderProgramCompiler().compile(
        ctx,
        StrategyAction(mode=StrategyMode.CANCEL_REPRICE, cancel_age_ticks=2),
        valuation,
    )
    decision = compiled.as_decision()
    assert len(decision.replaces) == 1
    assert decision.replaces[0].order_id == 7
    assert decision.replaces[0].replacement.interval == ctx.primary_interval
