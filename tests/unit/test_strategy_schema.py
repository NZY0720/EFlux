"""Unit tests for the strategy schema (StrategyMode / StrategyAction / programs)."""

from __future__ import annotations

from decimal import Decimal

from eflux.agents.base import CancelIntent, OrderIntent, ReplaceIntent
from eflux.agents.strategy import CompiledProgram, StrategyAction, StrategyMode


def test_strategy_action_defaults_to_noop_passive():
    a = StrategyAction()
    assert a.mode is StrategyMode.NOOP
    assert a.aggressiveness == 0.0 and a.qty_fraction == 1.0 and a.price_offset_bps == 0.0
    assert a.price_target_mult is None


def test_strategy_mode_serializes_as_str():
    # str-Enum so it round-trips through LLM/PPO I/O and audit records.
    assert StrategyMode.LADDER_SELL == "ladder_sell"
    assert StrategyMode("cover_deficit") is StrategyMode.COVER_DEFICIT


def test_compiled_program_empty_and_flatten():
    assert CompiledProgram().is_empty
    prog = CompiledProgram(
        order_intents=[OrderIntent("buy", Decimal("50"), Decimal("1"))],
        cancel_intents=[CancelIntent(1)],
        replace_intents=[ReplaceIntent(2, Decimal("51"), Decimal("1"))],
    )
    assert not prog.is_empty
    flat = prog.as_intent_list()
    # cancels, then replaces, then new orders
    assert [type(x).__name__ for x in flat] == ["CancelIntent", "ReplaceIntent", "OrderIntent"]
