"""Market event types — published to Redis Streams for downstream consumers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EventKind(StrEnum):
    ORDER_SUBMITTED = "order.submitted"
    ORDER_CANCELLED = "order.cancelled"
    TRADE = "trade"
    EXTERNAL_TRADE = "external.trade"
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
    interval_id: str
    delivery_start: datetime
    delivery_end: datetime
    purpose: str


class TradeEvent(_Base):
    kind: Literal[EventKind.TRADE] = Field(default=EventKind.TRADE)
    trade_id: int
    buy_order_id: int
    sell_order_id: int
    buy_vpp_id: int
    sell_vpp_id: int
    price: Decimal
    qty: Decimal
    interval_id: str
    delivery_start: datetime
    delivery_end: datetime
    buy_purpose: str
    sell_purpose: str


class ExternalTradeEvent(_Base):
    kind: Literal[EventKind.EXTERNAL_TRADE] = Field(default=EventKind.EXTERNAL_TRADE)
    external_trade_id: int
    vpp_id: int
    side: str
    price: Decimal
    raw_lmp: Decimal
    qty: Decimal
    region: str
    node: str
    counterparty: str = "CAISO SP15"
    interval_start: datetime | None = None
    interval_end: datetime | None = None


class TickEvent(_Base):
    """Periodic clock tick. Carries a market snapshot summary."""

    kind: Literal[EventKind.TICK] = Field(default=EventKind.TICK)
    tick_no: int
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    last_price: Decimal | None = None
    external_price: Decimal | None = None
    import_price: Decimal | None = None
    export_price: Decimal | None = None
    bid_depth: Decimal = Decimal("0")
    ask_depth: Decimal = Decimal("0")
    interval_id: str
    delivery_start: datetime
    delivery_end: datetime


MarketEvent = OrderEvent | TradeEvent | ExternalTradeEvent | TickEvent
