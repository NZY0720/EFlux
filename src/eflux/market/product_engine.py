"""Multi-product continuous double auction for explicit delivery intervals.

This is the V1 venue.  Each delivery product has an isolated order book while
order/trade ids remain globally unique.  Prices may be negative; quantity is
strictly positive terminal kWh.  Gate closure is authoritative and removes all
unfilled exposure before physical delivery begins.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from eflux.market.delivery import OrderPurpose
from eflux.market.order_book import LimitOrder, OrderBook
from eflux.market.products import DeliveryInterval, TimeInForce


@dataclass
class ProductLimitOrder(LimitOrder):
    interval: DeliveryInterval = field(kw_only=True)
    purpose: OrderPurpose = field(kw_only=True)
    time_in_force: TimeInForce = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class ProductTrade:
    trade_id: int
    interval: DeliveryInterval
    sim_ts: datetime
    wall_ts: datetime
    buy_order_id: int
    sell_order_id: int
    buy_vpp_id: int
    sell_vpp_id: int
    buy_purpose: OrderPurpose
    sell_purpose: OrderPurpose
    price: Decimal
    qty: Decimal


@dataclass(frozen=True, slots=True)
class ProductOrderEvent:
    kind: str
    interval: DeliveryInterval
    sim_ts: datetime
    wall_ts: datetime
    order_id: int
    vpp_id: int
    side: str
    purpose: OrderPurpose
    price: Decimal
    qty: Decimal
    remaining_qty: Decimal


ProductMarketEvent = ProductTrade | ProductOrderEvent
PublishProductCb = Callable[[ProductMarketEvent], None]


@dataclass(frozen=True, slots=True)
class ProductMatchResult:
    order: ProductLimitOrder
    trades: tuple[ProductTrade, ...]
    killed: bool = False


class ProductMatchingEngine:
    def __init__(self, publish_cb: PublishProductCb | None = None) -> None:
        self.publish = publish_cb or (lambda _event: None)
        self._books: dict[str, OrderBook] = {}
        self._intervals: dict[str, DeliveryInterval] = {}
        self._order_to_interval: dict[int, str] = {}
        self._closed: set[str] = set()
        self._last_price: dict[str, Decimal] = {}
        self._latest_price: Decimal | None = None
        self._liquidity_provider_ids: set[int] = set()
        self._trade_count = 0
        self._next_order_id = 1
        self._next_trade_id = 1

    @property
    def intervals(self) -> tuple[DeliveryInterval, ...]:
        return tuple(sorted(self._intervals.values(), key=lambda interval: interval.start))

    def last_price(self, interval_id: str) -> Decimal | None:
        return self._last_price.get(interval_id)

    def interval(self, interval_id: str) -> DeliveryInterval:
        try:
            return self._intervals[interval_id]
        except KeyError as exc:
            raise KeyError(f"unknown delivery product {interval_id}") from exc

    @property
    def latest_price(self) -> Decimal | None:
        return self._latest_price

    @property
    def trade_count(self) -> int:
        return self._trade_count

    def iter_orders(self, interval_id: str, side: str) -> tuple[ProductLimitOrder, ...]:
        if side not in {"buy", "sell"}:
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        book = self._books.get(interval_id)
        if book is None:
            return ()
        return tuple(book.iter_orders(side))  # type: ignore[arg-type, return-value]

    def register(self, interval: DeliveryInterval) -> None:
        iid = interval.interval_id
        existing = self._intervals.get(iid)
        if existing is not None and existing != interval:
            raise ValueError(f"conflicting definition for delivery product {iid}")
        self._intervals[iid] = interval
        self._books.setdefault(iid, OrderBook())

    def register_liquidity_provider(self, participant_id: int) -> None:
        """Mark a system counterparty allowed to trade real-price products.

        A ``realprice`` product is a price-taking external-grid venue, not a
        peer auction.  Ordinary participants may therefore match only against
        one of these ids; P2P products retain normal peer matching.
        """

        self._liquidity_provider_ids.add(participant_id)

    def allocate_order_id(self) -> int:
        """Reserve a globally unique id before risk/resource reservation.

        The gateway allocates first, uses the id as the reservation key, and
        passes it back to :meth:`submit`. Rejected ids are intentionally not
        reused, preserving deterministic audit history.
        """

        oid = self._next_order_id
        self._next_order_id += 1
        return oid

    def submit(
        self,
        *,
        interval: DeliveryInterval,
        vpp_id: int,
        side: str,
        purpose: OrderPurpose,
        price: Decimal,
        qty: Decimal,
        sim_ts: datetime,
        wall_ts: datetime,
        time_in_force: TimeInForce = TimeInForce.GOOD_TIL_GATE,
        ttl_sec: float | None = None,
        order_id: int | None = None,
    ) -> ProductMatchResult:
        self.register(interval)
        iid = interval.interval_id
        if iid in self._closed or not interval.is_trading_open(sim_ts):
            raise ValueError(f"delivery product {iid} is not open for trading")
        if side not in {"buy", "sell"}:
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        if not price.is_finite():
            raise ValueError("price must be finite")
        if not qty.is_finite() or qty <= 0:
            raise ValueError("qty must be finite and positive")
        if ttl_sec is not None and ttl_sec <= 0.0:
            raise ValueError("ttl_sec must be positive when set")

        oid = self.allocate_order_id() if order_id is None else order_id
        if oid <= 0:
            raise ValueError("order_id must be positive")
        if oid in self._order_to_interval:
            raise ValueError(f"order_id {oid} is already resting")
        if order_id is not None:
            self._next_order_id = max(self._next_order_id, oid + 1)
        expires_at = interval.gate_closure
        if ttl_sec is not None:
            expires_at = min(expires_at, sim_ts + timedelta(seconds=ttl_sec))
        order = ProductLimitOrder(
            order_id=oid,
            vpp_id=vpp_id,
            side=side,  # type: ignore[arg-type]
            price=price,
            qty=qty,
            remaining_qty=qty,
            sim_ts=sim_ts.astimezone(UTC),
            seq=self._books[iid].next_seq(),
            expires_at=expires_at,
            dispatched=purpose != OrderPurpose.BALANCE,
            interval=interval,
            purpose=purpose,
            time_in_force=time_in_force,
        )

        if time_in_force == TimeInForce.FILL_OR_KILL and not self._can_fill(order):
            return ProductMatchResult(order=order, trades=(), killed=True)

        trades = tuple(self._match(order, wall_ts=wall_ts))
        should_rest = time_in_force == TimeInForce.GOOD_TIL_GATE
        if order.remaining_qty > 0 and should_rest:
            self._books[iid].add(order)
            self._order_to_interval[order.order_id] = iid
            self.publish(self._order_event("order.submitted", order, wall_ts))
        return ProductMatchResult(order=order, trades=trades)

    def get(self, order_id: int) -> ProductLimitOrder | None:
        iid = self._order_to_interval.get(order_id)
        if iid is None:
            return None
        order = self._books[iid].get(order_id)
        return order if isinstance(order, ProductLimitOrder) else None

    def cancel(
        self, order_id: int, *, sim_ts: datetime, wall_ts: datetime
    ) -> ProductLimitOrder | None:
        iid = self._order_to_interval.pop(order_id, None)
        if iid is None:
            return None
        removed = self._books[iid].cancel(order_id)
        if not isinstance(removed, ProductLimitOrder):
            return None
        self.publish(self._order_event("order.cancelled", removed, wall_ts, sim_ts=sim_ts))
        return removed

    def close_interval(
        self, interval_id: str, *, sim_ts: datetime, wall_ts: datetime
    ) -> tuple[ProductLimitOrder, ...]:
        interval = self._intervals.get(interval_id)
        if interval is None:
            raise KeyError(interval_id)
        if sim_ts.astimezone(UTC) < interval.gate_closure:
            raise ValueError("cannot close a delivery product before gate closure")
        if interval_id in self._closed:
            return ()
        removed: list[ProductLimitOrder] = []
        for side in ("buy", "sell"):
            for order in list(self._books[interval_id].iter_orders(side)):  # type: ignore[arg-type]
                gone = self.cancel(order.order_id, sim_ts=sim_ts, wall_ts=wall_ts)
                if gone is not None:
                    removed.append(gone)
        self._closed.add(interval_id)
        return tuple(removed)

    def close_due(self, *, sim_ts: datetime, wall_ts: datetime) -> tuple[ProductLimitOrder, ...]:
        removed: list[ProductLimitOrder] = []
        for iid, interval in sorted(self._intervals.items(), key=lambda item: item[1].start):
            if iid not in self._closed and sim_ts.astimezone(UTC) >= interval.gate_closure:
                removed.extend(self.close_interval(iid, sim_ts=sim_ts, wall_ts=wall_ts))
        return tuple(removed)

    def expire(self, *, sim_ts: datetime, wall_ts: datetime) -> tuple[ProductLimitOrder, ...]:
        removed: list[ProductLimitOrder] = []
        for iid, book in self._books.items():
            if iid in self._closed:
                continue
            expired = [
                order
                for side in ("buy", "sell")
                for order in book.iter_orders(side)  # type: ignore[arg-type]
                if order.expires_at is not None and order.expires_at <= sim_ts
            ]
            for order in expired:
                gone = self.cancel(order.order_id, sim_ts=sim_ts, wall_ts=wall_ts)
                if gone is not None:
                    removed.append(gone)
        return tuple(removed)

    def open_orders_for_vpp(
        self, vpp_id: int, *, interval_id: str | None = None
    ) -> tuple[ProductLimitOrder, ...]:
        ids = [interval_id] if interval_id is not None else list(self._books)
        return tuple(
            order
            for iid in ids
            if iid in self._books
            for side in ("buy", "sell")
            for order in self._books[iid].iter_orders(side)  # type: ignore[arg-type]
            if order.vpp_id == vpp_id
        )

    def snapshot(self, interval_id: str, *, depth_levels: int = 10) -> dict:
        interval = self._intervals.get(interval_id)
        if interval is None:
            raise KeyError(interval_id)
        book = self._books[interval_id]
        bb, ba = book.best_bid(), book.best_ask()
        return {
            "interval_id": interval_id,
            "delivery_start": interval.start.isoformat(),
            "delivery_end": interval.end.isoformat(),
            "gate_closure": interval.gate_closure.isoformat(),
            "is_closed": interval_id in self._closed,
            "best_bid": str(bb.price) if bb else None,
            "best_ask": str(ba.price) if ba else None,
            "last_price": str(self._last_price[interval_id])
            if interval_id in self._last_price
            else None,
            "bids": [(str(p), str(q)) for p, q in book.depth("buy", depth_levels)],
            "asks": [(str(p), str(q)) for p, q in book.depth("sell", depth_levels)],
        }

    def _match(self, taker: ProductLimitOrder, *, wall_ts: datetime) -> list[ProductTrade]:
        iid = taker.interval.interval_id
        book = self._books[iid]
        opposite: Literal["buy", "sell"] = "sell" if taker.side == "buy" else "buy"
        trades: list[ProductTrade] = []
        while taker.remaining_qty > 0:
            resting = self._best_maker(book, opposite, taker)
            if resting is None:
                break
            fill_qty = min(taker.remaining_qty, resting.remaining_qty)
            taker.remaining_qty -= fill_qty
            resting.remaining_qty -= fill_qty
            book.reduce(resting, fill_qty)
            if resting.remaining_qty <= 0:
                self._order_to_interval.pop(resting.order_id, None)
            buy = taker if taker.side == "buy" else resting
            sell = resting if taker.side == "buy" else taker
            trade = ProductTrade(
                trade_id=self._next_trade_id,
                interval=taker.interval,
                sim_ts=taker.sim_ts,
                wall_ts=wall_ts.astimezone(UTC),
                buy_order_id=buy.order_id,
                sell_order_id=sell.order_id,
                buy_vpp_id=buy.vpp_id,
                sell_vpp_id=sell.vpp_id,
                buy_purpose=buy.purpose,
                sell_purpose=sell.purpose,
                price=resting.price,
                qty=fill_qty,
            )
            self._next_trade_id += 1
            self._last_price[iid] = resting.price
            self._latest_price = resting.price
            self._trade_count += 1
            trades.append(trade)
            self.publish(trade)
        return trades

    def _can_fill(self, taker: ProductLimitOrder) -> bool:
        book = self._books[taker.interval.interval_id]
        opposite: Literal["buy", "sell"] = "sell" if taker.side == "buy" else "buy"
        available = Decimal("0")
        for level in book._book(opposite).values():
            if taker.side == "buy" and taker.price < level.price:
                break
            if taker.side == "sell" and taker.price > level.price:
                break
            available += sum(
                order.remaining_qty
                for order in level.orders
                if self._eligible_counterparty(taker, order)
            )
            if available >= taker.qty:
                return True
        return False

    def _best_maker(
        self,
        book: OrderBook,
        opposite_side: Literal["buy", "sell"],
        taker: ProductLimitOrder,
    ) -> ProductLimitOrder | None:
        for level in book._book(opposite_side).values():
            if taker.side == "buy" and taker.price < level.price:
                break
            if taker.side == "sell" and taker.price > level.price:
                break
            for order in level.orders:
                if self._eligible_counterparty(taker, order):
                    return order  # type: ignore[return-value]
        return None

    def _eligible_counterparty(self, taker: ProductLimitOrder, maker: ProductLimitOrder) -> bool:
        if maker.vpp_id == taker.vpp_id:
            return False
        if taker.interval.market != "realprice":
            return True
        return (
            taker.vpp_id in self._liquidity_provider_ids
            or maker.vpp_id in self._liquidity_provider_ids
        )

    @staticmethod
    def _order_event(
        kind: str,
        order: ProductLimitOrder,
        wall_ts: datetime,
        *,
        sim_ts: datetime | None = None,
    ) -> ProductOrderEvent:
        return ProductOrderEvent(
            kind=kind,
            interval=order.interval,
            sim_ts=(sim_ts or order.sim_ts).astimezone(UTC),
            wall_ts=wall_ts.astimezone(UTC),
            order_id=order.order_id,
            vpp_id=order.vpp_id,
            side=order.side,
            purpose=order.purpose,
            price=order.price,
            qty=order.qty,
            remaining_qty=order.remaining_qty,
        )
