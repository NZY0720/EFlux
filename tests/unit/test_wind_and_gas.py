"""Unit tests for the wind turbine model, load profiles, and the gas agent."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.gas import GasGeneratorAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad, WindTurbine

# --- WindTurbine ------------------------------------------------------------


def test_power_curve_regions():
    wt = WindTurbine(rated_kw=10.0)
    assert wt._power_curve(1.0) == 0.0  # below cut-in
    assert wt._power_curve(12.0) == 10.0  # at rated speed
    assert wt._power_curve(20.0) == 10.0  # between rated and cut-out
    assert wt._power_curve(26.0) == 0.0  # storm cut-out
    mid = wt._power_curve(7.5)  # halfway → (0.5)^3 = 12.5%
    assert 1.0 < mid < 2.0


def test_stub_wind_produces_power_around_mean():
    wt = WindTurbine(rated_kw=10.0, mean_wind=8.0)
    rng = random.Random(7)
    ts = datetime(2026, 6, 10, 12, tzinfo=UTC)
    outputs = [wt.output_kw(ts, rng) for _ in range(200)]
    assert all(0.0 <= o <= 10.0 for o in outputs)
    # 8 m/s mean → meaningful output most of the time.
    assert sum(outputs) / len(outputs) > 1.0


def test_wind_uses_weather_dataframe_when_attached():
    pd = __import__("pytest").importorskip("pandas")
    ts = datetime(2026, 6, 10, 12, tzinfo=UTC)
    df = pd.DataFrame(
        {"wind_speed": [13.0]},  # above rated speed → rated output
        index=pd.DatetimeIndex([pd.Timestamp(ts)]),
    )
    wt = WindTurbine(rated_kw=10.0, mean_wind=0.0)
    wt.weather = df
    rng = random.Random(1)
    # AR(1) needs a few ticks to converge from the hourly base; just check it
    # climbs toward rated rather than sitting at the (zero) stub mean.
    out = [wt.output_kw(ts, rng) for _ in range(120)][-1]
    assert out > 5.0


# --- FlexibleLoad profiles ----------------------------------------------------


def _draw(profile: str, hour: int, weekday: bool = True) -> float:
    # 2026-06-10 is a Wednesday; 2026-06-13 a Saturday.
    day = 10 if weekday else 13
    load = FlexibleLoad(base_kw=10.0, noise_std=0.0, profile=profile)
    return load.draw_kw(datetime(2026, 6, day, hour, tzinfo=UTC), random.Random(0))


def test_industrial_profile_shift_vs_night():
    assert _draw("industrial", 12) == 10.0  # full shift
    assert _draw("industrial", 2) == 3.5  # night crew
    assert _draw("industrial", 12, weekday=False) < 5.0  # weekend slowdown


def test_commercial_and_flat_profiles():
    assert _draw("commercial", 14) == 10.0
    assert _draw("commercial", 3) == 2.5
    assert _draw("flat", 4) == 10.0
    assert _draw("flat", 15) == 10.0


# --- GasGeneratorAgent ---------------------------------------------------------


def _gas_ctx(gas_kw_max: float, cost: float = 60.0) -> AgentContext:
    params = VPPParams(
        gas_kw_max=gas_kw_max, gas_cost_per_mwh=cost, battery_kwh=0.0, battery_kw_max=0.0
    )
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=0.0)
    market = MarketSnapshot(
        sim_ts=state.sim_ts, best_bid=None, best_ask=None, last_price=None, mid_price=None
    )
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(kw_peak=0.0),
        battery=Battery(capacity_kwh=0.0, max_power_kw=0.0),
        load=FlexibleLoad(base_kw=0.0),
        market=market,
        rng=random.Random(0),
        tick_duration_h=1.0 / 3600.0,
    )


def test_gas_agent_offers_capacity_at_marginal_cost():
    agent = GasGeneratorAgent()
    ctx = _gas_ctx(gas_kw_max=30.0, cost=58.0)
    decision = agent.decide(ctx)
    assert len(decision.orders) == 1
    offer = decision.orders[0]
    assert offer.side == "sell"
    assert offer.purpose.value == "dispatchable"
    assert offer.price == Decimal("58.0000")
    # 30 kW over a five-minute delivery interval = 2.5 kWh.
    assert offer.qty_kwh == Decimal("2.5000")


def test_gas_agent_silent_without_capacity():
    agent = GasGeneratorAgent()
    ctx = _gas_ctx(gas_kw_max=0.0)
    assert agent.decide(ctx).is_empty
