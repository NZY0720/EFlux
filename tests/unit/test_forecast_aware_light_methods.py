from __future__ import annotations

import json
import random
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from eflux.agents.aa_agent import AAAgent
from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.gd_agent import GDAgent
from eflux.agents.hybrid import HybridPolicyAgent
from eflux.agents.reflective.strategist import LLMStrategist, build_strategist_user_message
from eflux.agents.strategy.policy import (
    BaselinePolicy,
    BatteryAwareStrategyPolicy,
    ScriptedStrategyPolicy,
)
from eflux.agents.valuation import TruthfulValuationOracle, ValuationSignal
from eflux.forecasting.schema import ForecastBundle, ForecastPoint, TargetForecast
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _target(v5m: float, v1h: float, v12h: float) -> TargetForecast:
    return TargetForecast(
        h5m=ForecastPoint(v5m, 0.1),
        h1h=ForecastPoint(v1h, 0.2),
        h12h=ForecastPoint(v12h, 0.3),
    )


def _forecast(*, p2p_1h: float = 80.0, real_1h: float = 90.0) -> ForecastBundle:
    return ForecastBundle(
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        model_version="unit",
        price_real=_target(50.0, real_1h, 95.0),
        price_p2p=_target(50.0, p2p_1h, 85.0),
        ghi=_target(300.0, 600.0, 100.0),
        temp_air=_target(20.0, 21.0, 22.0),
        wind_speed=_target(3.0, 4.0, 5.0),
    )


def _ctx(
    *,
    pv_kw: float = 5.0,
    load_kw: float = 1.0,
    soc_kwh: float = 5.0,
    forecast: ForecastBundle | None = None,
) -> AgentContext:
    params = VPPParams(markup_floor=1.0)
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=soc_kwh, pv_kw=pv_kw, load_kw=load_kw)
    state.update_net()
    state.pending_net_kwh = state.net_kw
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
        forecast=forecast,
    )


def _valuation(*, surplus: float = 1.0, deficit: float = 0.0, trend: float = 0.0) -> ValuationSignal:
    return ValuationSignal(
        fair_buy_price=50.0,
        fair_sell_price=50.0,
        marginal_battery_value=50.0,
        battery_sell_price=55.0,
        battery_buy_price=45.0,
        surplus_kwh=surplus,
        deficit_kwh=deficit,
        soc_frac=0.5,
        soc_pressure=0.0,
        expected_ref_1h=50.0 * (1.0 + trend) if trend else None,
        expected_ref_12h=None,
        price_trend=trend,
    )


def test_oracle_populates_forward_fields_from_rising_forecast():
    sig = TruthfulValuationOracle(price_ref=Decimal("50.0")).estimate(
        _ctx(forecast=_forecast(p2p_1h=80.0))
    )

    assert sig.expected_ref_1h == 80.0
    assert sig.expected_ref_12h == 85.0
    assert sig.price_trend > 0.0


def test_oracle_forecast_none_keeps_forward_defaults_and_legacy_fields():
    sig = TruthfulValuationOracle(price_ref=Decimal("50.0")).estimate(_ctx(forecast=None))
    expected = TruthfulValuationOracle(price_ref=Decimal("50.0")).estimate(_ctx(forecast=None))

    assert sig.expected_ref_1h is None
    assert sig.expected_ref_12h is None
    assert sig.price_trend == 0.0
    legacy_keys = set(asdict(sig)) - {"expected_ref_1h", "expected_ref_12h", "price_trend"}
    assert {k: asdict(sig)[k] for k in legacy_keys} == {k: asdict(expected)[k] for k in legacy_keys}


def test_baseline_policy_opt_in_rising_forecast_raises_sell_multiplier():
    ctx = _ctx(forecast=_forecast())
    val = _valuation(trend=0.5)

    off = BaselinePolicy(GDAgent(price_ref=Decimal("50.0"))).select_action(ctx, val)
    on = BaselinePolicy(GDAgent(price_ref=Decimal("50.0")), use_forecast=True).select_action(ctx, val)

    assert on.price_target_mult is not None and off.price_target_mult is not None
    assert on.price_target_mult > off.price_target_mult


def test_baseline_policy_default_off_ignores_forecast_signal():
    no_forecast = BaselinePolicy(AAAgent(price_ref=Decimal("50.0"))).select_action(
        _ctx(forecast=None),
        _valuation(trend=0.0),
    )
    with_forecast = BaselinePolicy(AAAgent(price_ref=Decimal("50.0"))).select_action(
        _ctx(forecast=_forecast()),
        _valuation(trend=0.5),
    )

    assert with_forecast == no_forecast


def test_price_mult_forecast_tilt_moves_both_sides_with_trend():
    from eflux.agents.strategy.policy import _tilt_price_mult

    ctx = _ctx()
    sell_base = 1.2
    buy_base = 0.9

    sell_rising = _tilt_price_mult(
        sell_base,
        side="sell",
        ctx=ctx,
        valuation=_valuation(trend=0.5),
        enabled=True,
    )
    sell_falling = _tilt_price_mult(
        sell_base,
        side="sell",
        ctx=ctx,
        valuation=_valuation(trend=-0.5),
        enabled=True,
    )
    buy_rising = _tilt_price_mult(
        buy_base,
        side="buy",
        ctx=ctx,
        valuation=_valuation(trend=0.5),
        enabled=True,
    )
    buy_falling = _tilt_price_mult(
        buy_base,
        side="buy",
        ctx=ctx,
        valuation=_valuation(trend=-0.5),
        enabled=True,
    )

    assert sell_rising > sell_base
    assert 1.0 <= sell_falling <= sell_base
    assert buy_base < buy_rising <= 1.0
    assert buy_falling < buy_base
    assert _tilt_price_mult(sell_base, side="sell", ctx=ctx, valuation=_valuation(trend=0.5), enabled=False) == sell_base
    assert _tilt_price_mult(buy_base, side="buy", ctx=ctx, valuation=_valuation(trend=-0.5), enabled=False) == buy_base


def test_scripted_and_battery_opt_in_rising_forecast_raise_soc_target():
    ctx = _ctx(forecast=_forecast())
    val = _valuation(surplus=0.0, trend=0.5)

    scripted_off = ScriptedStrategyPolicy().select_action(ctx, val)
    scripted_on = ScriptedStrategyPolicy(use_forecast=True).select_action(ctx, val)
    battery_off = BatteryAwareStrategyPolicy().select_action(ctx, val)
    battery_on = BatteryAwareStrategyPolicy(use_forecast=True).select_action(ctx, val)

    assert scripted_on.soc_target > scripted_off.soc_target
    assert battery_on.soc_target > battery_off.soc_target


def test_strategist_user_message_includes_forecast_only_when_supplied():
    base = build_strategist_user_message(
        recent_pnl=[],
        soc_frac=0.5,
        best_bid=48.0,
        best_ask=52.0,
        last_price=50.0,
    )
    with_forecast = build_strategist_user_message(
        recent_pnl=[],
        soc_frac=0.5,
        best_bid=48.0,
        best_ask=52.0,
        last_price=50.0,
        forecast=_forecast().to_dict(),
    )

    assert "forecast" not in json.loads(base)
    data = json.loads(with_forecast)
    assert data["forecast"]["price_p2p"]["1h"] == 80.0
    assert data["forecast"]["price_real"]["12h"] == 95.0
    assert data["forecast"]["ghi"]["5m"] == 300.0


@pytest.mark.asyncio
async def test_hybrid_refresh_threads_context_forecast_into_strategist_payload():
    class FakeClient:
        def __init__(self):
            self.messages = None

        async def chat(self, messages, *, temperature=0.2):
            self.messages = messages
            return '{"risk_budget": 0.5}'

    client = FakeClient()
    agent = HybridPolicyAgent(
        strategist=LLMStrategist(client=client),
        refresh_every_n_ticks=1,
    )

    agent.decide(_ctx(forecast=_forecast(p2p_1h=77.12345)))
    await agent._reflection_task

    payload = json.loads(client.messages[1]["content"])
    assert payload["forecast"]["price_p2p"]["1h"] == 77.1235
