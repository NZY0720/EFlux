"""Unit tests for the strategy schema (StrategyMode / StrategyAction / programs)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.decision import CancelRequest, OrderRequest, ReplaceRequest
from eflux.agents.strategy import CompiledProgram, StrategyAction, StrategyMode
from eflux.market.delivery import OrderPurpose
from eflux.market.products import next_delivery_interval


def test_strategy_action_defaults_to_noop_passive():
    a = StrategyAction()
    assert a.mode is StrategyMode.NOOP
    assert a.aggressiveness == 0.0 and a.qty_fraction == 1.0 and a.price_offset_bps == 0.0
    assert a.price_target_mult is None


def test_strategy_mode_serializes_as_str():
    # str-Enum so it round-trips through LLM/PPO I/O and audit records.
    assert StrategyMode.LADDER_SELL == "ladder_sell"
    assert StrategyMode("cover_deficit") is StrategyMode.COVER_DEFICIT


def test_compiled_program_converts_to_canonical_decision():
    assert CompiledProgram().is_empty
    interval = next_delivery_interval(datetime.now(UTC))
    order = OrderRequest("buy", Decimal("50"), Decimal("1"), interval, OrderPurpose.BATTERY)
    prog = CompiledProgram(
        order_requests=[order],
        cancel_requests=[CancelRequest(1)],
        replace_requests=[ReplaceRequest(2, order)],
    )
    assert not prog.is_empty
    decision = prog.as_decision()
    assert decision.orders == (order,)
    assert decision.cancels[0].order_id == 1
    assert decision.replaces[0].order_id == 2
