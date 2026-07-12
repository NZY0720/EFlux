"""HybridPolicyAgent tests (M6): assembly, guidance application, fallback hook, roster."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.hybrid import HybridPolicyAgent
from eflux.agents.reflective.strategist import StaticStrategist, StrategyGuidance
from eflux.agents.strategy.policy import BaselinePolicy
from eflux.agents.strategy.schema import StrategyMode
from eflux.agents.truthful import TruthfulAgent
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _make_ctx(
    *, pv_kw: float, load_kw: float, soc_kwh: float = 5.0, markup_floor: float = 0.1
) -> AgentContext:
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
        battery=Battery(
            capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max, soc_kwh=soc_kwh
        ),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=market,
        rng=random.Random(0),
        tick_duration_h=1.0,
    )


def test_hybrid_without_strategist_matches_scripted_strategy():
    # No guidance → behaves like the scripted StrategyAgent (a sell of the surplus).
    decision = HybridPolicyAgent(price_ref=Decimal("50.0")).decide(
        _make_ctx(pv_kw=5.0, load_kw=1.0)
    )
    assert len(decision.orders) == 1 and decision.orders[0].side == "sell"
    assert decision.orders[0].qty_kwh == Decimal("4.0000")


def test_hybrid_guidance_scales_order_size():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0)
    decision = HybridPolicyAgent(
        price_ref=Decimal("50.0"),
        strategist=StaticStrategist(StrategyGuidance(risk_budget=0.5)),
    ).decide(ctx)
    # risk_budget 0.5 halves the quoted quantity (soft prior), same side/price.
    assert decision.orders[0].side == "sell"
    assert decision.orders[0].qty_kwh == Decimal("2.0000")


def test_hybrid_guidance_can_bias_preferred_primitive():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0)
    decision = HybridPolicyAgent(
        price_ref=Decimal("50.0"),
        strategist=StaticStrategist(StrategyGuidance(preferred_modes=(StrategyMode.LADDER_SELL,))),
    ).decide(ctx)

    assert len(decision.orders) == 3
    assert {order.side for order in decision.orders} == {"sell"}
    assert sum(order.qty_kwh for order in decision.orders) == Decimal("3.9999")


def test_hybrid_guidance_mode_pin_flows_into_compiled_orders():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, soc_kwh=5.0)
    decision = HybridPolicyAgent(
        price_ref=Decimal("50.0"),
        strategist=StaticStrategist(
            StrategyGuidance(mode_pin=StrategyMode.BATTERY_ARBITRAGE, soc_target=0.4)
        ),
    ).decide(ctx)

    assert len(decision.orders) == 1
    assert decision.orders[0].side == "sell"
    assert decision.orders[0].purpose.value == "battery"
    # Default 3 kW inverter over a five-minute product can deliver 0.25 kWh.
    assert decision.orders[0].qty_kwh == Decimal("0.2500")


def test_managed_truthful_llm_adapter_emits_deficit_order_without_error():
    """The managed truthful+LLM assembly uses TruthfulAgent inside BaselinePolicy."""
    agent = HybridPolicyAgent(
        price_ref=Decimal("50.0"),
        executor=BaselinePolicy(TruthfulAgent(price_ref=Decimal("50.0"))),
        strategist=StaticStrategist(StrategyGuidance(price_bias_bps=10.0)),
    )

    decision = agent.decide(_make_ctx(pv_kw=0.0, load_kw=3.0))

    assert decision.orders and decision.orders[0].side == "buy"
    assert decision.orders[0].purpose.value == "balance"


def test_hybrid_influence_stats_count_guidance_changes():
    agent = HybridPolicyAgent(
        price_ref=Decimal("50.0"),
        strategist=StaticStrategist(StrategyGuidance(mode_pin=StrategyMode.LADDER_SELL)),
    )

    agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))

    assert agent.influence_stats == {
        "guided_ticks": 1,
        "guidance_change_rate": 1.0,
        "mode_override_rate": 1.0,
        "avg_price_dev_bps": None,
    }


def test_hybrid_record_trade_tracks_price_deviation_from_fair_value():
    agent = HybridPolicyAgent(price_ref=Decimal("50.0"))
    agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))

    agent.record_trade({"price": "5.5", "qty": "1", "side": "sell"})

    assert agent.influence_stats["avg_price_dev_bps"] == pytest.approx(1000.0)


def test_hybrid_exposes_guidance_diagnostics():
    g = StrategyGuidance(
        avoid_modes=(),
        mode_pin=StrategyMode.COVER_DEFICIT,
        halt=True,
        passive_only=True,
        risk_budget=0.4,
        price_bias_bps=15.0,
        soc_target=0.55,
        lesson="be patient",
    )
    agent = HybridPolicyAgent(strategist=StaticStrategist(g))
    agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))
    diag = agent.diagnostics["guidance"]
    assert diag["risk_budget"] == 0.4 and diag["lesson"] == "be patient"
    assert diag["mode_pin"] == "cover_deficit"
    assert diag["halt"] is True
    assert diag["passive_only"] is True
    assert diag["price_bias_bps"] == 15.0


def test_hybrid_exposes_risk_fallback_for_the_runner_hook():
    agent = HybridPolicyAgent()
    # The runner's gate-fallback hook reads .risk_fallback; default is stand down.
    assert agent.risk_fallback is None


def test_hybrid_regime_note_flags_oversupply_illiquidity_and_full_soc():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, soc_kwh=9.5)
    ctx.market.last_price = Decimal("10.0")
    ctx.market.best_bid = None
    agent = HybridPolicyAgent(price_ref=Decimal("50.0"))

    note = agent._regime_note(ctx)

    assert "oversupply" in note
    assert "illiquid" in note
    assert "near full" in note


def test_hybrid_regime_note_flags_scarcity():
    ctx = _make_ctx(pv_kw=1.0, load_kw=5.0)
    ctx.market.best_ask = None
    agent = HybridPolicyAgent(price_ref=Decimal("50.0"))

    assert "scarce" in agent._regime_note(ctx)

    ctx.market.best_ask = Decimal("70.0")
    assert "scarcity" in agent._regime_note(ctx)


def test_hybrid_regime_note_balanced_for_benign_market():
    agent = HybridPolicyAgent(price_ref=Decimal("50.0"))

    assert agent._regime_note(_make_ctx(pv_kw=2.0, load_kw=2.0)) == "balanced market"


def test_fallback_policy_truthful_restores_legacy_hook():
    agent = HybridPolicyAgent(fallback_policy="truthful")
    assert isinstance(agent.risk_fallback, TruthfulAgent)


def test_fallback_policy_invalid_raises():
    with pytest.raises(ValueError, match="fallback_policy must be one of"):
        HybridPolicyAgent(fallback_policy="silent-truthful")


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

    agent = HybridPolicyAgent(
        strategist=LLMStrategist(client=FakeClient()), refresh_every_n_ticks=1
    )
    decision = agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))
    assert len(decision.orders) == 1  # decided fine; no background task scheduled without a loop
    assert agent._reflection_task is None


@pytest.mark.asyncio
async def test_hybrid_schedules_offline_refresh_under_running_loop():
    from eflux.agents.reflective.strategist import LLMStrategist

    class FakeClient:
        async def chat(self, messages, *, temperature=0.2):
            return '{"risk_budget": 0.5, "soc_target": 0.6}'

    agent = HybridPolicyAgent(
        strategist=LLMStrategist(client=FakeClient()), refresh_every_n_ticks=1
    )
    agent.decide(_make_ctx(pv_kw=5.0, load_kw=1.0))  # tick 1 → schedules a refresh
    assert agent._reflection_task is not None
    await agent._reflection_task  # let the off-path refresh complete
    g = agent.strategist.current_guidance()
    assert g is not None and g.risk_budget == 0.5


@pytest.mark.asyncio
async def test_hybrid_refresh_threads_regime_note_into_strategist_payload():
    from eflux.agents.reflective.strategist import LLMStrategist

    class FakeClient:
        def __init__(self):
            self.messages = None

        async def chat(self, messages, *, temperature=0.2):
            self.messages = messages
            return '{"risk_budget": 0.5}'

    client = FakeClient()
    agent = HybridPolicyAgent(
        price_ref=Decimal("50.0"),
        strategist=LLMStrategist(client=client),
        refresh_every_n_ticks=1,
    )
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0)
    ctx.market.last_price = Decimal("10.0")
    ctx.market.best_bid = None

    agent.decide(ctx)
    assert agent._reflection_task is not None
    await agent._reflection_task

    payload = json.loads(client.messages[1]["content"])
    assert payload["regime_note"]
    assert "oversupply" in payload["regime_note"]
    assert "illiquid" in payload["regime_note"]
