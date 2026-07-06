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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from eflux.agents.base import AgentContext
from eflux.agents.strategy.schema import (
    PRICE_MULT_MAX,
    PRICE_MULT_MIN,
    StrategyAction,
    StrategyMode,
)
from eflux.agents.valuation import ValuationSignal

if TYPE_CHECKING:
    from eflux.agents._baseline_common import BaselineAgent


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


@dataclass
class BatteryAwareStrategyPolicy:
    """Deterministic PPO-space demonstrator for the battery-buffer environment.

    It first clears forced imbalance, then uses the battery primitive only when price
    is on the right side of the oracle's battery value and SOC has room inside the
    demonstrator guard band. This keeps BC targets in the four PPO modes while avoiding
    invalid battery actions at the SOC extremes.
    """

    min_qty: float = 0.01
    battery_buy_price_mult: float = 1.2
    battery_sell_price_mult: float = 1.0
    battery_aggressiveness: float = 1.0

    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        min_qty = max(0.0, float(self.min_qty))
        surplus = max(0.0, float(valuation.surplus_kwh or 0.0))
        deficit = max(0.0, float(valuation.deficit_kwh or 0.0))
        soc = max(0.0, min(1.0, float(valuation.soc_frac or 0.0)))
        last = self._last_price(ctx)

        if deficit >= min_qty:
            return StrategyAction(mode=StrategyMode.COVER_DEFICIT)

        if surplus >= min_qty:
            fair_sell = float(valuation.fair_sell_price or 0.0)
            if last is None or last >= fair_sell:
                return StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS)
            return StrategyAction(mode=StrategyMode.NOOP)

        if last is not None:
            battery_buy = float(valuation.battery_buy_price or 0.0)
            battery_sell = float(valuation.battery_sell_price or 0.0)
            if battery_buy > 0.0 and last <= battery_buy * self.battery_buy_price_mult and soc < 0.9:
                return StrategyAction(
                    mode=StrategyMode.BATTERY_ARBITRAGE,
                    aggressiveness=self.battery_aggressiveness,
                    soc_target=0.9,
                )
            if battery_sell > 0.0 and last >= battery_sell * self.battery_sell_price_mult and soc > 0.2:
                return StrategyAction(
                    mode=StrategyMode.BATTERY_ARBITRAGE,
                    aggressiveness=self.battery_aggressiveness,
                    soc_target=0.2,
                )

        return StrategyAction(mode=StrategyMode.NOOP)

    def _last_price(self, ctx: AgentContext) -> float | None:
        if ctx.market.last_price is not None:
            return float(ctx.market.last_price)
        if ctx.market.mid_price is not None:
            return float(ctx.market.mid_price)
        return None


@dataclass
class BaselinePolicy:
    """Adapt a classical CDA baseline (AA / ZIP / GD / Truthful) into the `StrategyPolicy`
    seam so the slow LLM strategist can coach it *exactly* like the PPO executor.

    The wrapped baseline computes its own target quote price via its `_quote_price` rule
    (AA's p*/aggressiveness, ZIP's adaptive margin, GD's belief maximiser); we express that
    price as a `StrategyAction` over the shared action space via `price_target_mult`. With no
    guidance the neutral action reproduces the standalone baseline byte-for-byte (the compiler's
    `_anchor` recovers `limit * (target/limit) == target`); when guidance arrives,
    `apply_guidance` layers the LLM's binding mode_pin + soft risk/price/soc bias on top. The
    oracle → compiler → RiskGate pipeline and the baseline's own individual-rationality clamp
    are reused unchanged. Fills are forwarded so the baseline's online adaptation keeps learning.
    """

    base: BaselineAgent

    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        min_qty = float(self.base.min_qty)
        if valuation.surplus_kwh >= min_qty:
            side, limit, mode = "sell", valuation.fair_sell_price, StrategyMode.LIQUIDATE_SURPLUS
        elif valuation.deficit_kwh >= min_qty:
            side, limit, mode = "buy", valuation.fair_buy_price, StrategyMode.COVER_DEFICIT
        else:
            return StrategyAction(mode=StrategyMode.NOOP)
        anchor = float(limit)
        if anchor <= 0:
            return StrategyAction(mode=mode)
        target = self.base._quote_price(side=side, limit=anchor, ctx=ctx, sig=valuation)
        # Reuse the baseline's individual-rationality clamp: a seller never prices below its
        # marginal cost (mult ≥ 1), a buyer never above its marginal value (mult ≤ 1).
        target = self.base._rationalize(side, target, anchor)
        mult = max(PRICE_MULT_MIN, min(PRICE_MULT_MAX, target / anchor))
        return StrategyAction(mode=mode, price_target_mult=mult)

    def record_trade(self, record: dict) -> None:
        self.base.record_trade(record)
