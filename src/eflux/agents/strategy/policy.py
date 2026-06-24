"""Strategy policy seam.

`StrategyPolicy.select_action` is the single decision point a tactical policy implements:
given the market/VPP context and the valuation signal (and, later, slow LLM guidance), it
chooses one `StrategyAction`. This is the seam every policy plugs into without touching the
agent, compiler, or risk gate:

- `ScriptedStrategyPolicy` (here) — a deterministic baseline mirroring Truthful's
  imbalance logic.
- `PPOPrimitiveAgent` (M4) — a learned policy over the same action space.
- LLM-guided policy (M6) — the scripted/PPO policy biased by `StrategyGuidance`.

Keeping the action space identical across all three is what lets PPO learn behaviour that
departs from Truthful while staying interpretable and risk-gated (design note §5.2, §8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eflux.agents.base import AgentContext
from eflux.agents.strategy.schema import StrategyAction, StrategyMode
from eflux.agents.valuation import ValuationSignal


@runtime_checkable
class StrategyPolicy(Protocol):
    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        ...


@dataclass
class ScriptedStrategyPolicy:
    """Deterministic baseline policy: trade the ambient imbalance, else stand down.

    Mirrors the Truthful agent's primary behaviour within the one-action-per-tick
    contract that PPO will inherit — surplus → liquidate, deficit → cover, balanced →
    no-op. Battery-band arbitrage is left to the learned policy (the primitive exists
    and is gated; sizing it well across cadences is exactly what PPO should learn)."""

    min_qty: float = 0.01

    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        if valuation.surplus_kwh >= self.min_qty:
            return self._guided(
                StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS),
                "sell",
                guidance,
            )
        if valuation.deficit_kwh >= self.min_qty:
            return self._guided(
                StrategyAction(mode=StrategyMode.COVER_DEFICIT),
                "buy",
                guidance,
            )
        return self._guided(StrategyAction(mode=StrategyMode.NOOP), "neutral", guidance)

    def _guided(
        self,
        base: StrategyAction,
        side: str,
        guidance: object | None,
    ) -> StrategyAction:
        """Adopt a preferred primitive only when it is compatible with the current
        imbalance. The guidance remains soft: incompatible preferences are ignored,
        and avoided base modes are merely shrunk later by apply_guidance()."""
        preferred = tuple(getattr(guidance, "preferred_modes", ()) or ())
        if not preferred:
            return base
        compatible = _COMPATIBLE_MODES[side]
        for mode in preferred:
            if mode in compatible:
                return _action_for_preferred(mode)
        return base


_COMPATIBLE_MODES: dict[str, set[StrategyMode]] = {
    "sell": {
        StrategyMode.LIQUIDATE_SURPLUS,
        StrategyMode.LADDER_SELL,
        StrategyMode.AGGRESSIVE_TAKER,
        StrategyMode.PASSIVE_MARKET_MAKE,
        StrategyMode.BATTERY_ARBITRAGE,
    },
    "buy": {
        StrategyMode.COVER_DEFICIT,
        StrategyMode.LADDER_BUY,
        StrategyMode.AGGRESSIVE_TAKER,
        StrategyMode.PASSIVE_MARKET_MAKE,
        StrategyMode.BATTERY_ARBITRAGE,
    },
    "neutral": {
        StrategyMode.NOOP,
        StrategyMode.HOLD_ENERGY,
        StrategyMode.PASSIVE_MARKET_MAKE,
        StrategyMode.BATTERY_ARBITRAGE,
    },
}


def _action_for_preferred(mode: StrategyMode) -> StrategyAction:
    if mode in (StrategyMode.LADDER_SELL, StrategyMode.LADDER_BUY):
        return StrategyAction(mode=mode, ladder_levels=3, ladder_slope=0.01)
    if mode == StrategyMode.AGGRESSIVE_TAKER:
        return StrategyAction(mode=mode, aggressiveness=1.0)
    if mode == StrategyMode.PASSIVE_MARKET_MAKE:
        return StrategyAction(mode=mode, qty_fraction=0.5, ladder_slope=0.02)
    return StrategyAction(mode=mode)
