"""Runner-level tests for HybridPolicyAgent fallback policy telemetry."""

from __future__ import annotations

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.hybrid import HybridPolicyAgent
from eflux.agents.strategy.schema import StrategyAction, StrategyMode
from eflux.agents.valuation import ValuationSignal
from eflux.bridge.bus import InMemoryBus
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams


class _OutOfBandPolicy:
    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        return StrategyAction(
            mode=StrategyMode.BATTERY_ARBITRAGE,
            price_target_mult=4.0,
            price_offset_bps=-1000000.0,
            soc_target=0.0,
        )


def _tick_once(sim: Simulator, vpp) -> None:
    sim_ts = sim.clock.now_sim()
    market = MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot(depth_levels=5))
    vpp.pv.output_kw = lambda sim_ts, rng: 5.0
    sim._tick_vpp(vpp, sim_ts, 1.0, market)


def test_hold_fallback_policy_counts_veto_hold_and_submits_nothing():
    sim = Simulator(bus=InMemoryBus())
    agent = HybridPolicyAgent(executor=_OutOfBandPolicy(), fallback_policy="hold")
    vpp = sim.add_builtin_vpp(
        "hold",
        VPPParams(pv_kw_peak=5.0, load_kw_base=0.0, load_profile="flat"),
        agent,
    )

    _tick_once(sim, vpp)

    assert sim.engine.book.best_ask() is None
    assert sim.veto_holds_by_vpp[vpp.vpp_id] == 1
    assert sim.fallback_invocations_by_vpp.get(vpp.vpp_id, 0) == 0
    assert sim.decide_ticks_by_vpp[vpp.vpp_id] == 1


def test_truthful_fallback_policy_requotes_and_counts_invocation():
    sim = Simulator(bus=InMemoryBus())
    agent = HybridPolicyAgent(executor=_OutOfBandPolicy(), fallback_policy="truthful")
    vpp = sim.add_builtin_vpp(
        "truthful",
        VPPParams(pv_kw_peak=5.0, load_kw_base=0.0, load_profile="flat"),
        agent,
    )

    _tick_once(sim, vpp)

    assert sim.engine.book.best_ask() is not None
    assert sim.fallback_invocations_by_vpp[vpp.vpp_id] == 1
    assert sim.veto_holds_by_vpp.get(vpp.vpp_id, 0) == 0
    assert sim.decide_ticks_by_vpp[vpp.vpp_id] == 1
