"""CDA matching engine — price-time priority, in-process.

On every new order, walk the opposite side of the book and fill as much as possible at
resting-order prices (price improvement to the taker). The remainder, if any, joins the book.

Trades and order events are emitted via the publish_cb callback so the caller can route
them anywhere (Redis Stream, in-memory queue, DB writer).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from eflux.market.events import EventKind, MarketEvent, OrderEvent, TradeEvent
from eflux.market.order_book import LimitOrder, OrderBook

PublishCb = Callable[[MarketEvent], None]


@dataclass
class MatchResult:
    order: LimitOrder
    trades: list[TradeEvent]


class MatchingEngine:
    def __init__(self, publish_cb: PublishCb | None = None) -> None:
        self.book = OrderBook()
        self.publish = publish_cb or (lambda _e: None)
        self._next_order_id = 1
        self._next_trade_id = 1
        self.last_price: Decimal | None = None

    def _alloc_order_id(self) -> int:
        oid = self._next_order_id
        self._next_order_id += 1
        return oid

    def _alloc_trade_id(self) -> int:
        tid = self._next_trade_id
        self._next_trade_id += 1
        return tid

    def submit(
        self,
        *,
        vpp_id: int,
        side: str,
        price: Decimal,
        qty: Decimal,
        sim_ts: datetime,
        wall_ts: datetime,
        order_id: int | None = None,
        ttl_sec: float | None = None,
        dispatched: bool = False,
    ) -> MatchResult:
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        if qty <= 0:
            raise ValueError("qty must be positive")
        if price <= 0:
            raise ValueError("price must be positive")

        oid = order_id if order_id is not None else self._alloc_order_id()
        order = LimitOrder(
            order_id=oid,
            vpp_id=vpp_id,
            side=side,  # type: ignore[arg-type]
            price=price,
            qty=qty,
            remaining_qty=qty,
            sim_ts=sim_ts,
            seq=self.book.next_seq(),
            expires_at=sim_ts + timedelta(seconds=ttl_sec) if ttl_sec else None,
            dispatched=dispatched,
        )

        trades = self._match(order, sim_ts=sim_ts, wall_ts=wall_ts)

        if order.remaining_qty > 0:
            self.book.add(order)
            self.publish(
                OrderEvent(
                    kind=EventKind.ORDER_SUBMITTED,
                    sim_ts=sim_ts,
                    wall_ts=wall_ts,
                    order_id=order.order_id,
                    vpp_id=order.vpp_id,
                    side=order.side,
                    price=order.price,
                    qty=order.qty,
                    remaining_qty=order.remaining_qty,
                )
            )

        return MatchResult(order=order, trades=trades)

    def cancel(self, order_id: int, *, sim_ts: datetime, wall_ts: datetime) -> bool:
        removed = self.book.cancel(order_id)
        if removed is None:
            return False
        self.publish(
            OrderEvent(
                kind=EventKind.ORDER_CANCELLED,
                sim_ts=sim_ts,
                wall_ts=wall_ts,
                order_id=removed.order_id,
                vpp_id=removed.vpp_id,
                side=removed.side,
                price=removed.price,
                qty=removed.qty,
                remaining_qty=removed.remaining_qty,
            )
        )
        return True

    def expire(self, *, sim_ts: datetime, wall_ts: datetime) -> list[LimitOrder]:
        """Cancel resting orders whose TTL has lapsed; return the removed orders.

        Without expiry, quotes that never cross (e.g. gas asks above every bid)
        accumulate forever and the book depth stops meaning anything. Linear
        sweep — the book holds at most a few orders per participant.
        """
        expired = [
            o
            for side in ("buy", "sell")
            for o in self.book.iter_orders(side)  # type: ignore[arg-type]
            if o.expires_at is not None and o.expires_at <= sim_ts
        ]
        removed: list[LimitOrder] = []
        for order in expired:
            gone = self.book.cancel(order.order_id)
            if gone is None:
                continue
            removed.append(gone)
            self.publish(
                OrderEvent(
                    kind=EventKind.ORDER_CANCELLED,
                    sim_ts=sim_ts,
                    wall_ts=wall_ts,
                    order_id=gone.order_id,
                    vpp_id=gone.vpp_id,
                    side=gone.side,
                    price=gone.price,
                    qty=gone.qty,
                    remaining_qty=gone.remaining_qty,
                )
            )
        return removed

    def _match(
        self, taker: LimitOrder, *, sim_ts: datetime, wall_ts: datetime
    ) -> list[TradeEvent]:
        trades: list[TradeEvent] = []
        opposite_side = "sell" if taker.side == "buy" else "buy"

        while taker.remaining_qty > 0:
            opp_book = self.book._book(opposite_side)  # noqa: SLF001
            if not opp_book:
                break
            best_level = opp_book.peekitem(0)[1]
            # Check price crossing.
            if taker.side == "buy" and taker.price < best_level.price:
                break
            if taker.side == "sell" and taker.price > best_level.price:
                break

            resting = best_level.orders[0]
            fill_qty = min(taker.remaining_qty, resting.remaining_qty)
            fill_price = resting.price  # price-time priority: resting price wins

            taker.remaining_qty -= fill_qty
            resting.remaining_qty -= fill_qty
            best_level.total_qty -= fill_qty
            self.last_price = fill_price

            buy_order = taker if taker.side == "buy" else resting
            sell_order = resting if taker.side == "buy" else taker

            trade = TradeEvent(
                trade_id=self._alloc_trade_id(),
                sim_ts=sim_ts,
                wall_ts=wall_ts,
                buy_order_id=buy_order.order_id,
                sell_order_id=sell_order.order_id,
                buy_vpp_id=buy_order.vpp_id,
                sell_vpp_id=sell_order.vpp_id,
                price=fill_price,
                qty=fill_qty,
            )
            trades.append(trade)
            self.publish(trade)

            if resting.remaining_qty <= 0:
                self.book.remove_filled(resting)

        return trades

    def snapshot(self, depth_levels: int = 10) -> dict:
        bb = self.book.best_bid()
        ba = self.book.best_ask()
        return {
            "best_bid": str(bb.price) if bb else None,
            "best_ask": str(ba.price) if ba else None,
            "last_price": str(self.last_price) if self.last_price is not None else None,
            "bids": [(str(p), str(q)) for p, q in self.book.depth("buy", depth_levels)],
            "asks": [(str(p), str(q)) for p, q in self.book.depth("sell", depth_levels)],
        }
