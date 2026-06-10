"""Limit order book — price-time priority, in-memory.

Uses sortedcontainers.SortedDict for O(log n) best-price access. Each price level holds
a deque of orders (FIFO for time priority). Total size at a level is precomputed for fast depth queries.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sortedcontainers import SortedDict

Side = Literal["buy", "sell"]


@dataclass
class LimitOrder:
    order_id: int
    vpp_id: int
    side: Side
    price: Decimal
    qty: Decimal
    remaining_qty: Decimal
    sim_ts: datetime
    seq: int = 0  # tie-breaker for time priority


@dataclass
class PriceLevel:
    price: Decimal
    orders: deque[LimitOrder] = field(default_factory=deque)
    total_qty: Decimal = Decimal("0")

    def add(self, order: LimitOrder) -> None:
        self.orders.append(order)
        self.total_qty += order.remaining_qty

    def remove(self, order_id: int) -> LimitOrder | None:
        # O(n) — acceptable for typical depth.
        for i, o in enumerate(self.orders):
            if o.order_id == order_id:
                del self.orders[i]
                self.total_qty -= o.remaining_qty
                return o
        return None


class OrderBook:
    """Two sides: bids sorted DESC (best = highest), asks sorted ASC (best = lowest).

    SortedDict natively sorts ASC, so for bids we key on (-price) to flip ordering.
    """

    def __init__(self) -> None:
        self._bids: SortedDict[Decimal, PriceLevel] = SortedDict()  # key = -price
        self._asks: SortedDict[Decimal, PriceLevel] = SortedDict()  # key = +price
        self._order_index: dict[int, tuple[Side, Decimal]] = {}  # order_id -> (side, level_key)
        self._seq: int = 0

    def _level_key(self, side: Side, price: Decimal) -> Decimal:
        return -price if side == "buy" else price

    def _book(self, side: Side) -> SortedDict[Decimal, PriceLevel]:
        return self._bids if side == "buy" else self._asks

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def add(self, order: LimitOrder) -> None:
        key = self._level_key(order.side, order.price)
        book = self._book(order.side)
        level = book.get(key)
        if level is None:
            level = PriceLevel(price=order.price)
            book[key] = level
        level.add(order)
        self._order_index[order.order_id] = (order.side, key)

    def cancel(self, order_id: int) -> LimitOrder | None:
        loc = self._order_index.pop(order_id, None)
        if loc is None:
            return None
        side, key = loc
        book = self._book(side)
        level = book.get(key)
        if level is None:
            return None
        removed = level.remove(order_id)
        if level.total_qty <= 0:
            del book[key]
        return removed

    def best_bid(self) -> PriceLevel | None:
        if not self._bids:
            return None
        return self._bids.peekitem(0)[1]

    def best_ask(self) -> PriceLevel | None:
        if not self._asks:
            return None
        return self._asks.peekitem(0)[1]

    def depth(self, side: Side, levels: int = 10) -> list[tuple[Decimal, Decimal]]:
        """Return [(price, total_qty), ...] for top N levels."""
        book = self._book(side)
        out: list[tuple[Decimal, Decimal]] = []
        for i in range(min(levels, len(book))):
            level = book.peekitem(i)[1]
            out.append((level.price, level.total_qty))
        return out

    def crossing(self) -> tuple[LimitOrder, LimitOrder] | None:
        """Return (best_bid_order, best_ask_order) if bid >= ask, else None."""
        bb = self.best_bid()
        ba = self.best_ask()
        if bb is None or ba is None:
            return None
        if bb.price < ba.price:
            return None
        return bb.orders[0], ba.orders[0]

    def remove_filled(self, order: LimitOrder) -> None:
        side, key = self._order_index[order.order_id]
        book = self._book(side)
        level = book[key]
        # Order is at the head of the level (we only fully fill from head during matching).
        if level.orders and level.orders[0].order_id == order.order_id:
            level.orders.popleft()
        del self._order_index[order.order_id]
        if not level.orders:
            del book[key]

    def update_level_qty_after_partial(self, order: LimitOrder, filled_qty: Decimal) -> None:
        side, key = self._order_index[order.order_id]
        self._book(side)[key].total_qty -= filled_qty
