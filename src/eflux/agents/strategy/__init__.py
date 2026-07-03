"""Strategy layer — the structured trading language (design note §4).

A policy emits a `StrategyAction` (primitive + tactical parameters); the
`OrderProgramCompiler` deterministically lowers it through an `OrderProgram` into
concrete order / cancel / replace intents. This replaces "an agent speaks only in raw
OrderIntents" with a richer, bounded, interpretable action space that PPO and LLM-guided
policies can share.
"""

from __future__ import annotations

from eflux.agents.strategy.compiler import OrderProgramCompiler
from eflux.agents.strategy.policy import ScriptedStrategyPolicy, StrategyPolicy
from eflux.agents.strategy.schema import (
    PRICE_MULT_MAX,
    PRICE_MULT_MIN,
    CancelPolicy,
    CompiledProgram,
    OrderProgram,
    OrderSpec,
    StrategyAction,
    StrategyMode,
)

__all__ = [
    "PRICE_MULT_MAX",
    "PRICE_MULT_MIN",
    "CancelPolicy",
    "CompiledProgram",
    "OrderProgram",
    "OrderProgramCompiler",
    "OrderSpec",
    "ScriptedStrategyPolicy",
    "StrategyAction",
    "StrategyMode",
    "StrategyPolicy",
]
