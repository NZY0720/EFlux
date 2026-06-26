"""Participant-redesign features: the 'ev' load profile and gas-as-tradeable-supply.

Gas providers now run the normal bidding strategies (Truthful / ZIP / GD / AA / compiled
PPO) instead of only the dedicated GasGeneratorAgent: the valuation oracle meters fuel
capacity onto the sell side at marginal cost on a throttle cadence, marked
`supply_dispatched` so the resulting sells settle through fuel (dispatched=True).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.hybrid import StrategyAgent
from eflux.agents.strategy.policy import ScriptedStrategyPolicy
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.valuation import TruthfulValuationOracle
from eflux.agents.zip_agent import ZIPAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


# --------------------------------------------------------------------------- EV load profile
def test_ev_profile_charges_evening_and_overnight_not_midday():
    load = FlexibleLoad(base_kw=10.0, profile="ev", noise_std=0.0)
    rng = random.Random(0)
    day = datetime(2024, 6, 21, tzinfo=UTC)

    midday = load.draw_kw(day.replace(hour=12), rng)
    evening = load.draw_kw(day.replace(hour=20), rng)
    overnight = load.draw_kw(day.replace(hour=2), rng)

    assert evening > midday * 3, (evening, midday)        # plug-in peak dwarfs daytime
    assert overnight > midday * 3, (overnight, midday)    # overnight charge too
    assert midday < 2.0                                   # ~0.12 * 10 base


def test_ev_profile_differs_from_residential():
    rng = random.Random(0)
    noon = datetime(2024, 6, 21, 12, tzinfo=UTC)
    ev = FlexibleLoad(base_kw=10.0, profile="ev", noise_std=0.0).draw_kw(noon, rng)
    res = FlexibleLoad(base_kw=10.0, profile="residential", noise_std=0.0).draw_kw(noon, rng)
    assert ev < res  # EV is near-idle midday; residential has a daytime floor


# --------------------------------------------------------------------------- gas as supply
_GAS_EPOCH = datetime(2024, 6, 21, 8, tzinfo=UTC)


def _gas_ctx(*, gas_kw_max: float = 20.0, gas_cost: float = 60.0, soc_kwh: float = 0.0,
             sim_ts: datetime | None = None) -> AgentContext:
    """A pure gas provider: fuel capacity, no storage, no pv/wind/load."""
    params = VPPParams(
        pv_kw_peak=0.0, battery_kwh=0.0, battery_kw_max=0.0, load_kw_base=0.0,
        gas_kw_max=gas_kw_max, gas_cost_per_kwh=gas_cost,
    )
    ts = sim_ts or _GAS_EPOCH
    state = VPPState(sim_ts=ts, soc_kwh=soc_kwh, pv_kw=0.0, load_kw=0.0)
    state.update_net()
    market = MarketSnapshot(
        sim_ts=ts, best_bid=Decimal("65"), best_ask=Decimal("70"),
        last_price=Decimal("66"), mid_price=Decimal("67"),
    )
    return AgentContext(
        vpp_id=1, params=params, state=state,
        pv=PV(kw_peak=0.0),
        battery=Battery(capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max, soc_kwh=soc_kwh),
        load=FlexibleLoad(base_kw=0.0),
        market=market, rng=random.Random(1), tick_duration_h=1.0,
    )


def _first_after_window(agent, ctx_fn, window: int = 30):
    """Drive the agent until the gas throttle fires; return that tick's dispatched sells.
    Each tick advances sim_ts by a second (the throttle is gated on the tick timestamp)."""
    for t in range(window + 1):
        intents = agent.decide(ctx_fn(sim_ts=_GAS_EPOCH + timedelta(seconds=t)))
        gas_sells = [i for i in intents if i.side == "sell" and i.dispatched]
        if gas_sells:
            return gas_sells
    return []


def test_oracle_meters_gas_supply_on_cadence():
    oracle = TruthfulValuationOracle(price_ref=Decimal("50.0"), gas_quote_every_n_ticks=5)
    sigs = [oracle.estimate(_gas_ctx(sim_ts=_GAS_EPOCH + timedelta(seconds=t))) for t in range(5)]
    # Surplus is zero between windows and a metered fuel block on the cadence tick.
    assert sigs[0].surplus_kwh == 0.0 and not sigs[0].supply_dispatched
    assert sigs[4].surplus_kwh > 0.0 and sigs[4].supply_dispatched
    assert sigs[4].fair_sell_price == 60.0  # gas marginal cost, not the renewable floor


def test_gas_throttle_advances_once_per_tick_not_per_estimate_call():
    """The throttle is gated on sim_ts, so calling estimate() twice in a tick (as the training
    env does) advances the cadence once — not twice."""
    oracle = TruthfulValuationOracle(price_ref=Decimal("50.0"), gas_quote_every_n_ticks=3)

    def at(t: int):
        return oracle.estimate(_gas_ctx(sim_ts=_GAS_EPOCH + timedelta(seconds=t)))

    at(0)
    at(0)  # tick 0, two estimate() calls advance the cadence once -> counter = 1
    pre = at(1)
    at(1)  # tick 1, two calls -> counter = 2 (window=3, not yet)
    assert not pre.supply_dispatched
    fired = at(2)  # tick 2 -> counter = 3 -> fires
    assert fired.supply_dispatched


def test_gas_provider_with_renewable_or_load_is_rejected():
    from eflux.simulator.agent_spec import validate_vpp_params

    for bad in ({"gas_kw_max": 20.0, "pv_kw_peak": 5.0},
                {"gas_kw_max": 20.0, "wind_kw_rated": 8.0},
                {"gas_kw_max": 20.0, "load_kw_base": 3.0},
                {"gas_kw_max": 20.0, "battery_kwh": 12.0},
                {"gas_kw_max": 20.0, "battery_kw_max": 4.0}):
        with pytest.raises(ValueError, match="gas_kw_max"):
            validate_vpp_params(bad)
    # Pure gas is allowed when every non-fuel DER/load field is explicitly zeroed.
    validate_vpp_params(
        {
            "gas_kw_max": 20.0,
            "battery_kwh": 0.0,
            "battery_kw_max": 0.0,
            "pv_kw_peak": 0.0,
            "wind_kw_rated": 0.0,
            "load_kw_base": 0.0,
        }
    )


def test_truthful_gas_emits_dispatched_sell_at_marginal_cost():
    agent = TruthfulAgent(price_ref=Decimal("50.0"))
    gas_sells = _first_after_window(agent, _gas_ctx)
    assert gas_sells, "gas provider should offer fuel after the throttle window"
    sell = gas_sells[0]
    assert sell.dispatched is True            # settles through fuel, not the ambient balance
    assert sell.price == Decimal("60.0000")   # gas_cost_per_kwh
    assert sell.qty > 0


def test_zip_gas_emits_dispatched_sell_at_or_above_cost():
    agent = ZIPAgent(price_ref=Decimal("50.0"))
    gas_sells = _first_after_window(agent, _gas_ctx)
    assert gas_sells, "ZIP on a gas portfolio should offer fuel"
    sell = gas_sells[0]
    assert sell.dispatched is True
    assert sell.price >= Decimal("60.0000")   # ZIP margin never prices below marginal cost


def test_compiled_strategy_gas_path_is_dispatched():
    """The StrategyAgent/PPO compiled path (oracle → compiler) also marks gas sells dispatched."""
    agent = StrategyAgent(price_ref=Decimal("50.0"), policy=ScriptedStrategyPolicy())
    gas_sells = _first_after_window(agent, _gas_ctx)
    assert gas_sells, "scripted strategy on a gas portfolio should liquidate fuel surplus"
    assert all(i.dispatched for i in gas_sells)
    assert all(i.price >= Decimal("60.0000") for i in gas_sells)


def test_non_gas_portfolio_surplus_is_not_dispatched():
    """Regression: an ordinary PV-surplus seller is never marked dispatched."""
    params = VPPParams(pv_kw_peak=5.0, battery_kwh=10.0, markup_floor=0.4)
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=5.0, pv_kw=5.0, load_kw=1.0)
    state.update_net()
    state.pending_net_kwh = state.net_kw * 1.0
    market = MarketSnapshot(sim_ts=state.sim_ts, best_bid=Decimal("48"), best_ask=Decimal("52"),
                            last_price=Decimal("50"), mid_price=Decimal("50"))
    ctx = AgentContext(
        vpp_id=1, params=params, state=state, pv=PV(kw_peak=5.0),
        battery=Battery(capacity_kwh=10.0, max_power_kw=3.0, soc_kwh=5.0),
        load=FlexibleLoad(base_kw=1.5), market=market, rng=random.Random(0), tick_duration_h=1.0,
    )
    sig = TruthfulValuationOracle(price_ref=Decimal("50.0")).estimate(ctx)
    assert sig.supply_dispatched is False
    intents = TruthfulAgent(price_ref=Decimal("50.0")).decide(ctx)
    assert intents and all(not i.dispatched for i in intents if i.side == "sell")
