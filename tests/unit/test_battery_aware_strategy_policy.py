"""Decision-table tests for the BC battery-aware demonstrator."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.strategy.policy import BatteryAwareStrategyPolicy
from eflux.agents.strategy.schema import StrategyMode
from eflux.agents.valuation import ValuationSignal
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _ctx(*, last: float | None = 50.0, mid: float | None = 50.0) -> AgentContext:
    params = VPPParams()
    ts = datetime.now(UTC)
    state = VPPState(sim_ts=ts, soc_kwh=5.0, pv_kw=2.0, load_kw=2.0)
    state.update_net()
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(kw_peak=params.pv_kw_peak),
        battery=Battery(capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max, soc_kwh=5.0),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=MarketSnapshot(
            sim_ts=ts,
            best_bid=Decimal("48"),
            best_ask=Decimal("52"),
            last_price=Decimal(str(last)) if last is not None else None,
            mid_price=Decimal(str(mid)) if mid is not None else None,
        ),
        rng=random.Random(0),
        tick_duration_h=1.0,
    )


def _valuation(
    *,
    surplus: float = 0.0,
    deficit: float = 0.0,
    soc: float = 0.5,
    fair_sell: float = 45.0,
    fair_buy: float = 55.0,
    battery_sell: float = 55.0,
    battery_buy: float = 45.0,
) -> ValuationSignal:
    return ValuationSignal(
        fair_buy_price=fair_buy,
        fair_sell_price=fair_sell,
        marginal_battery_value=0.5 * (battery_sell + battery_buy),
        battery_sell_price=battery_sell,
        battery_buy_price=battery_buy,
        surplus_kwh=surplus,
        deficit_kwh=deficit,
        soc_frac=soc,
        soc_pressure=soc - 0.5,
    )


def test_deficit_covers_load_first():
    action = BatteryAwareStrategyPolicy().select_action(_ctx(last=20.0), _valuation(deficit=0.5, soc=0.1))
    assert action.mode is StrategyMode.COVER_DEFICIT


def test_overflow_liquidates_at_fair_price():
    action = BatteryAwareStrategyPolicy().select_action(_ctx(last=46.0), _valuation(surplus=0.5, fair_sell=45.0))
    assert action.mode is StrategyMode.LIQUIDATE_SURPLUS


def test_overflow_noops_when_price_collapses():
    action = BatteryAwareStrategyPolicy().select_action(_ctx(last=40.0), _valuation(surplus=0.5, fair_sell=45.0))
    assert action.mode is StrategyMode.NOOP


def test_cheap_price_charges_low_soc_battery():
    action = BatteryAwareStrategyPolicy().select_action(
        _ctx(last=44.0), _valuation(soc=0.3, battery_buy=45.0, battery_sell=55.0)
    )
    assert action.mode is StrategyMode.BATTERY_ARBITRAGE
    assert action.soc_target == pytest.approx(0.9)


def test_dear_price_discharges_high_soc_battery():
    action = BatteryAwareStrategyPolicy().select_action(
        _ctx(last=56.0), _valuation(soc=0.8, battery_buy=45.0, battery_sell=55.0)
    )
    assert action.mode is StrategyMode.BATTERY_ARBITRAGE
    assert action.soc_target == pytest.approx(0.2)


def test_balanced_fair_price_noops():
    action = BatteryAwareStrategyPolicy().select_action(
        _ctx(last=50.0), _valuation(soc=0.5, battery_buy=40.0, battery_sell=60.0)
    )
    assert action.mode is StrategyMode.NOOP
