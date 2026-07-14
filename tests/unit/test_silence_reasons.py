"""Closed-taxonomy coverage for strategy silence and strategist feedback."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.decision import SilenceReason
from eflux.agents.llm.strategist import (
    StrategyGuidance,
    apply_guidance,
    build_strategist_user_message,
)
from eflux.agents.strategy.compiler import OrderProgramCompiler
from eflux.agents.strategy.policy import ScriptedStrategyPolicy
from eflux.agents.strategy.schema import StrategyAction, StrategyMode
from eflux.agents.valuation import TruthfulValuationOracle
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _context(*, pending_kwh: float = 0.0, soc_kwh: float = 9.0) -> AgentContext:
    params = VPPParams()
    state = VPPState(
        sim_ts=datetime.now(UTC),
        soc_kwh=soc_kwh,
        pv_kw=max(0.0, pending_kwh),
        load_kw=max(0.0, -pending_kwh),
        pending_net_kwh=pending_kwh,
    )
    state.update_net()
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
            capacity_kwh=params.battery_kwh,
            max_power_kw=params.battery_kw_max,
            soc_kwh=soc_kwh,
        ),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=market,
        rng=random.Random(0),
        tick_duration_h=1.0,
        projected_net_kwh=pending_kwh,
    )


def _compile(ctx: AgentContext, action: StrategyAction):
    valuation = TruthfulValuationOracle().estimate(ctx)
    return OrderProgramCompiler().compile(ctx, action, valuation).as_decision()


def test_policy_noop_yields_policy_hold_code():
    ctx = _context()
    valuation = TruthfulValuationOracle().estimate(ctx)
    action = ScriptedStrategyPolicy().select_action(ctx, valuation)

    assert action.mode is StrategyMode.NOOP
    assert _compile(ctx, action).rationale == SilenceReason.POLICY_HOLD


def test_guidance_halt_yields_llm_hold_code():
    ctx = _context(pending_kwh=1.0)
    action = apply_guidance(
        StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS),
        StrategyGuidance(halt=True),
    )

    assert _compile(ctx, action).rationale == SilenceReason.LLM_HOLD


def test_battery_sized_to_zero_yields_zero_headroom_code():
    ctx = _context(soc_kwh=5.0)
    action = StrategyAction(mode=StrategyMode.BATTERY_ARBITRAGE, soc_target=0.5)

    assert _compile(ctx, action).rationale == SilenceReason.ZERO_HEADROOM


def test_sub_floor_order_yields_dust_code():
    ctx = _context(pending_kwh=0.005)
    action = StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS)

    assert _compile(ctx, action).rationale == SilenceReason.DUST


def test_strategist_context_contains_nonzero_silence_histogram_only():
    message = build_strategist_user_message(
        recent_pnl=[],
        soc_frac=0.5,
        best_bid=None,
        best_ask=None,
        last_price=None,
        silence_window={
            "silent_ticks": 3,
            "reasons": {"zero_headroom": 2, "dust": 1},
        },
    )
    assert json.loads(message)["silence_window"] == {
        "silent_ticks": 3,
        "reasons": {"zero_headroom": 2, "dust": 1},
    }

    empty = build_strategist_user_message(
        recent_pnl=[],
        soc_frac=0.5,
        best_bid=None,
        best_ask=None,
        last_price=None,
        silence_window={"silent_ticks": 0, "reasons": {}},
    )
    assert "silence_window" not in json.loads(empty)
