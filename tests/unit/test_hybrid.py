"""HybridPolicyAgent tests (M6): assembly, guidance application, fallback hook, roster."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.hybrid import HybridPolicyAgent
from eflux.agents.reflective.strategist import StaticStrategist, StrategyGuidance
from eflux.agents.truthful import TruthfulAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _make_ctx(*, pv_kw: float, load_kw: float, soc_kwh: float = 5.0, markup_floor: float = 0.1) -> AgentContext:
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


def test_hybrid_without_strategist_matches_scripted_strategy():
    # No guidance → behaves like the scripted StrategyAgent (a sell of the surplus).
    intents = HybridPolicyAgent(price_ref=Decimal("50.0")).decide(_make_ctx(pv_kw=5.0, load_kw=1.0))
    assert len(intents) == 1 and intents[0].side == "sell"
    assert intents[0].qty == Decimal("4.0000")


def test_hybrid_guidance_scales_order_size():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0)
    guided = HybridPolicyAgent(
        price_ref=Decimal("50.0"),
        strategist=StaticStrategist(StrategyGuidance(risk_budget=0.5)),
    ).decide(ctx)
    # risk_budget 0.5 halves the quoted quantity (soft prior), same side/price.
    assert guided[0].side == "sell" and guided[0].qty == Decimal("2.0000")


def test_hybrid_exposes_guidance_diagnostics():
    g = StrategyGuidance(avoid_modes=(), risk_budget=0.4, soc_target=0.55, lesson="be patient")
    agent = HybridPolicyAgent(strategist=StaticStrategist(g))
    agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))
    diag = agent.diagnostics["guidance"]
    assert diag["risk_budget"] == 0.4 and diag["lesson"] == "be patient"


def test_hybrid_exposes_risk_fallback_for_the_runner_hook():
    agent = HybridPolicyAgent()
    # The runner's gate-fallback hook reads .risk_fallback; default is a Truthful agent.
    assert isinstance(agent.risk_fallback, TruthfulAgent)


def test_hybrid_custom_fallback_is_used():
    fb = TruthfulAgent(price_ref=Decimal("42.0"))
    assert HybridPolicyAgent(fallback=fb).risk_fallback is fb


def test_hybrid_registered_in_agent_factories():
    from eflux.simulator.scenarios import AGENT_FACTORIES

    assert AGENT_FACTORIES["hybrid"] is HybridPolicyAgent
    assert isinstance(AGENT_FACTORIES["hybrid"](price_ref=Decimal("50.0")), HybridPolicyAgent)


def test_hybrid_sync_context_with_llm_strategist_does_not_crash():
    # No running loop (the bench/tests step synchronously) → refresh is skipped, the
    # agent still decides using whatever guidance is cached (None here).
    from eflux.agents.reflective.strategist import LLMStrategist

    class FakeClient:
        async def chat(self, messages, *, temperature=0.2):
            return "{}"

    agent = HybridPolicyAgent(strategist=LLMStrategist(client=FakeClient()), refresh_every_n_ticks=1)
    intents = agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))
    assert len(intents) == 1  # decided fine; no background task scheduled without a loop
    assert agent._reflection_task is None


@pytest.mark.asyncio
async def test_hybrid_schedules_offline_refresh_under_running_loop():
    from eflux.agents.reflective.strategist import LLMStrategist

    class FakeClient:
        async def chat(self, messages, *, temperature=0.2):
            return '{"risk_budget": 0.5, "soc_target": 0.6}'

    agent = HybridPolicyAgent(strategist=LLMStrategist(client=FakeClient()), refresh_every_n_ticks=1)
    agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))  # tick 1 → schedules a refresh
    assert agent._reflection_task is not None
    await agent._reflection_task  # let the off-path refresh complete
    g = agent.strategist.current_guidance()
    assert g is not None and g.risk_budget == 0.5
