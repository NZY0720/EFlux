"""Unit tests for StrategyAgent (oracle -> scripted policy -> compiler) and its
registration as a roster agent type."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.hybrid import StrategyAgent
from eflux.agents.truthful import TruthfulAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _make_ctx(*, pv_kw: float, load_kw: float, soc_kwh: float = 5.0, markup_floor: float = 0.0) -> AgentContext:
    params = VPPParams(markup_floor=markup_floor)
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=soc_kwh, pv_kw=pv_kw, load_kw=load_kw)
    state.update_net()
    state.pending_net_kwh = state.net_kw * 1.0
    market = MarketSnapshot(
        sim_ts=state.sim_ts, best_bid=Decimal("48"), best_ask=Decimal("52"),
        last_price=Decimal("50"), mid_price=Decimal("50"),
    )
    return AgentContext(
        vpp_id=1, params=params, state=state,
        pv=PV(kw_peak=params.pv_kw_peak),
        battery=Battery(capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max, soc_kwh=soc_kwh),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=market, rng=random.Random(0), tick_duration_h=1.0,
    )


def test_surplus_emits_sell_matching_truthful_balance_order():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    s = StrategyAgent(price_ref=Decimal("50.0")).decide(ctx)
    t = TruthfulAgent(price_ref=Decimal("50.0")).decide(ctx)
    assert len(s) == 1 and s[0].side == "sell"
    assert (s[0].price, s[0].qty) == (t[0].price, t[0].qty)


def test_deficit_emits_buy_matching_truthful_balance_order():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)
    s = StrategyAgent(price_ref=Decimal("50.0"), demand_beta=0.5).decide(ctx)
    t = TruthfulAgent(price_ref=Decimal("50.0"), demand_beta=0.5).decide(ctx)
    assert len(s) == 1 and s[0].side == "buy"
    assert (s[0].price, s[0].qty) == (t[0].price, t[0].qty)


def test_balanced_position_emits_nothing():
    assert StrategyAgent().decide(_make_ctx(pv_kw=2.0, load_kw=2.0)) == []


def test_scripted_policy_skips_battery_band():
    """Balanced ambient energy but SOC above the band: the scripted policy stands down
    (battery arbitrage is left for the learned policy), unlike Truthful's battery quote."""
    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, soc_kwh=9.0)  # soc 0.9 > soc_high
    ctx.state.pending_net_kwh = 0.0
    assert StrategyAgent().decide(ctx) == []


def test_registered_in_agent_factories():
    from eflux.simulator.scenarios import AGENT_FACTORIES

    assert AGENT_FACTORIES["strategy"] is StrategyAgent
    # Constructible the way the scenario loader does (agent_params from YAML).
    agent = AGENT_FACTORIES["strategy"](price_ref=Decimal("47.0"))
    assert isinstance(agent, StrategyAgent)


def test_agent_spec_accepts_strategy_kind():
    from eflux.simulator.agent_spec import AgentSpec

    spec = AgentSpec.model_validate(
        {
            "name": "s1",
            "agent": "strategy",
            "params": {"pv_kw_peak": 8.0, "battery_kwh": 15.0, "load_kw_base": 4.0},
            "agent_params": {"demand_beta": 0.5},
        }
    )
    assert spec.agent == "strategy"


def test_strategy_demo_scenario_file_validates():
    import yaml

    from eflux.config import PROJECT_ROOT
    from eflux.simulator.agent_spec import AgentSpec

    data = yaml.safe_load((PROJECT_ROOT / "scenarios" / "strategy_demo.yaml").read_text())
    specs = [AgentSpec.model_validate(entry) for entry in data["vpps"]]
    assert any(s.agent == "strategy" for s in specs)


def test_unknown_agent_params_rejected_at_load():
    import pytest

    from eflux.simulator.scenarios import _validate_agent_params

    # A truthful-only knob the scripted strategy agent does not accept.
    with pytest.raises(ValueError, match="soc_high"):
        _validate_agent_params("s1", {"soc_high": 0.5}, StrategyAgent)
    # The kwargs StrategyAgent does accept pass cleanly.
    _validate_agent_params("s1", {"price_ref": Decimal("50"), "demand_beta": 0.5}, StrategyAgent)
