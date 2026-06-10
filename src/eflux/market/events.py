"""Market event types — published to Redis Streams for downstream consumers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EventKind(str, Enum):
    ORDER_SUBMITTED = "order.submitted"
    ORDER_CANCELLED = "order.cancelled"
    TRADE = "trade"
    TICK = "tick"


class _Base(BaseModel):
    model_config = ConfigDict(use_enum_values=True, frozen=True)

    kind: EventKind
    sim_ts: datetime
    wall_ts: datetime


class OrderEvent(_Base):
    kind: Literal[EventKind.ORDER_SUBMITTED, EventKind.ORDER_CANCELLED]
    order_id: int
    vpp_id: int
    side: str
    price: Decimal
    qty: Decimal
    remaining_qty: Decimal


class TradeEvent(_Base):
    kind: Literal[EventKind.TRADE] = Field(default=EventKind.TRADE)
    trade_id: int
    buy_order_id: int
    sell_order_id: int
    buy_vpp_id: int
    sell_vpp_id: int
    price: Decimal
    qty: Decimal


class TickEvent(_Base):
    """Periodic clock tick. Carries a market snapshot summary."""

    kind: Literal[EventKind.TICK] = Field(default=EventKind.TICK)
    tick_no: int
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    last_price: Decimal | None = None
    bid_depth: Decimal = Decimal("0")
    ask_depth: Decimal = Decimal("0")


MarketEvent = OrderEvent | TradeEvent | TickEvent
