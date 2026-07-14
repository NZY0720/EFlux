"""Canonical V1 agent decision contract.

An agent decides against an immutable observation and returns declarative order,
cancel, and replace requests.  It never mutates the book or physical resources
directly; the gateway assigns ids, reserves resources, and submits atomically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from eflux.market.delivery import OrderPurpose
from eflux.market.products import DeliveryInterval, TimeInForce


class SilenceReason(StrEnum):
    POLICY_HOLD = "policy_hold"
    LLM_HOLD = "llm_hold"
    ZERO_HEADROOM = "zero_headroom"
    DUST = "dust"
    REJECTED = "rejected"


def classify_silence_reason(reason: str | None) -> SilenceReason:
    """Map legacy/free-text hold rationales into the closed silence taxonomy."""
    if reason is None:
        return SilenceReason.POLICY_HOLD
    try:
        return SilenceReason(reason)
    except ValueError:
        return SilenceReason.POLICY_HOLD


@dataclass(frozen=True, slots=True)
class OrderRequest:
    side: str
    price: Decimal
    qty_kwh: Decimal
    interval: DeliveryInterval
    purpose: OrderPurpose
    time_in_force: TimeInForce = TimeInForce.GOOD_TIL_GATE
    ttl_sec: float | None = None
    client_ref: str | None = None
    # Hybrid runs currently use best execution across peer and grid liquidity.
    # The explicit field keeps routing intent in training/evidence artifacts and
    # leaves room for venue-constrained execution without changing the schema.
    route: str = "auto"

    def __post_init__(self) -> None:
        if self.side not in {"buy", "sell"}:
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if not self.price.is_finite():
            raise ValueError("price must be finite")
        if not self.qty_kwh.is_finite() or self.qty_kwh <= 0:
            raise ValueError("qty_kwh must be finite and positive")
        if self.ttl_sec is not None and self.ttl_sec <= 0.0:
            raise ValueError("ttl_sec must be positive when set")
        if self.purpose == OrderPurpose.DISPATCHABLE and self.side != "sell":
            raise ValueError("dispatchable generation can only back sell orders")
        if self.purpose == OrderPurpose.FLEX_LOAD and self.side != "buy":
            raise ValueError("flexible load can only back buy orders")
        if self.client_ref is not None and len(self.client_ref) > 64:
            raise ValueError("client_ref cannot exceed 64 characters")
        if self.route not in {"auto", "peer", "grid"}:
            raise ValueError("route must be 'auto', 'peer', or 'grid'")


@dataclass(frozen=True, slots=True)
class CancelRequest:
    order_id: int

    def __post_init__(self) -> None:
        if self.order_id <= 0:
            raise ValueError("order_id must be positive")


@dataclass(frozen=True, slots=True)
class ReplaceRequest:
    order_id: int
    replacement: OrderRequest

    def __post_init__(self) -> None:
        if self.order_id <= 0:
            raise ValueError("order_id must be positive")


@dataclass(frozen=True, slots=True)
class AgentDecision:
    orders: tuple[OrderRequest, ...] = field(default_factory=tuple)
    cancels: tuple[CancelRequest, ...] = field(default_factory=tuple)
    replaces: tuple[ReplaceRequest, ...] = field(default_factory=tuple)
    rationale: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.orders or self.cancels or self.replaces)

    @classmethod
    def hold(cls, rationale: str | None = None) -> AgentDecision:
        return cls(rationale=rationale)
