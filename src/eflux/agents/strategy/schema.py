"""Structured trading language.

The core breakthrough of the agent-intelligence roadmap (design note §4): an agent no
longer speaks only in raw `OrderIntent`s. It selects a *strategy primitive* and its
parameters (`StrategyAction`); a deterministic compiler expands that into an
`OrderProgram` (a list of `OrderSpec`s plus a cancel policy), which lowers to concrete
intents (`CompiledProgram`). This gives PPO/LLM a much richer yet bounded, interpretable
action space than nudging Truthful's parameters.

Nothing here makes market calls or carries decision logic beyond parameter-driven
expansion — keep it pure and independently testable (design principle #6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from eflux.agents.base import CancelIntent, OrderIntent, ReplaceIntent

PRICE_MULT_MIN = 0.25
PRICE_MULT_MAX = 4.0


class StrategyMode(StrEnum):
    """The initial structured action library (design note §4). A str-Enum so it
    serializes cleanly into LLM/PPO I/O and audit records."""

    NOOP = "noop"
    HOLD_ENERGY = "hold_energy"
    LIQUIDATE_SURPLUS = "liquidate_surplus"
    COVER_DEFICIT = "cover_deficit"
    PASSIVE_MARKET_MAKE = "passive_market_make"
    AGGRESSIVE_TAKER = "aggressive_taker"
    LADDER_SELL = "ladder_sell"
    LADDER_BUY = "ladder_buy"
    CANCEL_REPRICE = "cancel_reprice"
    BATTERY_ARBITRAGE = "battery_arbitrage"
    GRID_CHARGE_ON_DIP = "grid_charge_on_dip"
    GRID_DISCHARGE_ON_PEAK = "grid_discharge_on_peak"
    WAIT_FOR_BETTER = "wait_for_better"


@dataclass(frozen=True)
class StrategyAction:
    """A trading primitive plus its tactical parameters — the unit a learned (or
    scripted) policy emits each tick. All knobs are bounded and dimensionless so the
    same action space is shared by the scripted policy, PPO, and LLM-guided policies."""

    mode: StrategyMode = StrategyMode.NOOP
    # 0 = passive (rest at the fair price, maker); 1 = cross to the opposite best
    # quote (taker). Interpolates between.
    aggressiveness: float = 0.0
    # Fraction of the available energy (surplus/deficit/SOC headroom) to quote.
    qty_fraction: float = 1.0
    # Extra price nudge in basis points of the order's base price (sells lower,
    # buys higher → improves fill odds). Applied after aggressiveness.
    price_offset_bps: float = 0.0
    # Multiplies this action's valuation anchor. None preserves legacy fair-price anchoring.
    price_target_mult: float | None = None
    # Ladder primitives: per-level price step as a fraction of the base price.
    ladder_slope: float = 0.0
    ladder_levels: int = 1
    # TTL for emitted orders in ticks (0 = use the runner default).
    ttl_ticks: int = 0
    # CANCEL_REPRICE: act on resting orders at/older than this age (0 = none).
    cancel_age_ticks: int = 0
    # Desired battery state of charge (0..1) — biases battery participation.
    soc_target: float = 0.5


@dataclass(frozen=True)
class OrderSpec:
    """One concrete order the program wants placed, before it becomes an intent."""

    side: str  # "buy" | "sell"
    price: Decimal
    qty: Decimal
    dispatched: bool = False
    ttl_ticks: int = 0


@dataclass(frozen=True)
class CancelPolicy:
    """How the program treats this VPP's existing resting orders."""

    cancel_all: bool = False
    # Cancel resting orders at/older than this age in ticks (0 = none).
    cancel_age_ticks: int = 0
    # When true, pair each cancelled order with a fresh same-side spec and emit a
    # ReplaceIntent instead of a bare cancel (no gap in the book).
    reprice: bool = False


@dataclass(frozen=True)
class OrderProgram:
    """The deterministic expansion of a StrategyAction: orders to place + a policy
    for existing orders, over a short horizon. Interpretable and auditable."""

    mode: StrategyMode
    orders: list[OrderSpec] = field(default_factory=list)
    cancel_policy: CancelPolicy = field(default_factory=CancelPolicy)
    horizon_ticks: int = 1
    rationale: str | None = None


@dataclass(frozen=True)
class CompiledProgram:
    """The lowered program: concrete intents the runner submits. Cancels/replaces
    are ordered before new orders so the book is freed before re-quoting."""

    order_intents: list[OrderIntent] = field(default_factory=list)
    cancel_intents: list[CancelIntent] = field(default_factory=list)
    replace_intents: list[ReplaceIntent] = field(default_factory=list)
    mode: StrategyMode = StrategyMode.NOOP
    rationale: str | None = None

    def as_intent_list(self) -> list[CancelIntent | ReplaceIntent | OrderIntent]:
        """Flatten to a single ordered list for a runner that dispatches by type."""
        return [*self.cancel_intents, *self.replace_intents, *self.order_intents]

    @property
    def is_empty(self) -> bool:
        return not (self.order_intents or self.cancel_intents or self.replace_intents)
