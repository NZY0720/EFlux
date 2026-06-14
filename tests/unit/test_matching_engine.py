"""Unit tests for the CDA matching engine."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from eflux.market.events import EventKind
from eflux.market.matching_engine import MatchingEngine


def _ts() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def published() -> list:
    return []


@pytest.fixture
def engine(published):
    return MatchingEngine(publish_cb=published.append)


def test_rejects_zero_or_negative_qty(engine):
    with pytest.raises(ValueError):
        engine.submit(vpp_id=1, side="buy", price=Decimal("10"), qty=Decimal("0"), sim_ts=_ts(), wall_ts=_ts())


def test_rejects_bad_side(engine):
    with pytest.raises(ValueError):
        engine.submit(vpp_id=1, side="hold", price=Decimal("10"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())


def test_resting_order_added_when_no_match(engine, published):
    r = engine.submit(vpp_id=1, side="buy", price=Decimal("50"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    assert r.trades == []
    assert r.order.remaining_qty == Decimal("1")
    # One ORDER_SUBMITTED event published.
    assert len(published) == 1
    assert published[0].kind == EventKind.ORDER_SUBMITTED.value


def test_full_fill_at_resting_price(engine, published):
    # Resting sell @ 50.
    engine.submit(vpp_id=1, side="sell", price=Decimal("50"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    published.clear()
    # Buyer crosses @ 55 → should fill @ 50 (price improvement to taker).
    r = engine.submit(vpp_id=2, side="buy", price=Decimal("55"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    assert len(r.trades) == 1
    trade = r.trades[0]
    assert trade.price == Decimal("50")
    assert trade.qty == Decimal("1")
    assert trade.buy_vpp_id == 2 and trade.sell_vpp_id == 1
    assert r.order.remaining_qty == Decimal("0")  # fully consumed
    assert engine.last_price == Decimal("50")


def test_price_time_priority_resting_first(engine):
    # Two resting sells, same price; first should fill first.
    engine.submit(vpp_id=10, side="sell", price=Decimal("50"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    engine.submit(vpp_id=11, side="sell", price=Decimal("50"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    r = engine.submit(vpp_id=99, side="buy", price=Decimal("50"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    assert len(r.trades) == 1
    assert r.trades[0].sell_vpp_id == 10  # FIFO at the level


def test_partial_fill_leaves_remainder_on_book(engine):
    engine.submit(vpp_id=1, side="sell", price=Decimal("50"), qty=Decimal("0.5"), sim_ts=_ts(), wall_ts=_ts())
    r = engine.submit(vpp_id=2, side="buy", price=Decimal("55"), qty=Decimal("1.0"), sim_ts=_ts(), wall_ts=_ts())
    assert len(r.trades) == 1
    assert r.trades[0].qty == Decimal("0.5")
    assert r.order.remaining_qty == Decimal("0.5")  # the buy's residual rests on the book

    snap = engine.snapshot(depth_levels=5)
    assert snap["bids"] == [("55", "0.5")]
    assert snap["asks"] == []


def test_no_cross_when_prices_dont_meet(engine):
    engine.submit(vpp_id=1, side="sell", price=Decimal("60"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    r = engine.submit(vpp_id=2, side="buy", price=Decimal("50"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    assert r.trades == []
    assert r.order.remaining_qty == Decimal("1")


def test_snapshot_reports_best_levels(engine):
    engine.submit(vpp_id=1, side="buy", price=Decimal("48"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    engine.submit(vpp_id=1, side="buy", price=Decimal("49"), qty=Decimal("2"), sim_ts=_ts(), wall_ts=_ts())
    engine.submit(vpp_id=2, side="sell", price=Decimal("51"), qty=Decimal("3"), sim_ts=_ts(), wall_ts=_ts())
    snap = engine.snapshot(depth_levels=5)
    assert snap["best_bid"] == "49"
    assert snap["best_ask"] == "51"
    # Top-of-book first for both sides.
    assert snap["bids"][0] == ("49", "2")
    assert snap["asks"][0] == ("51", "3")


def test_self_trade_prevented_skips_own_resting_order(engine):
    """An agent's incoming order must not wash-trade against its own resting
    quote: it should skip the same-vpp maker and match a genuine counterparty."""
    # Same VPP (5) rests an ask @ 52.70.
    engine.submit(vpp_id=5, side="sell", price=Decimal("52.70"), qty=Decimal("2"), sim_ts=_ts(), wall_ts=_ts())
    # A cheaper genuine ask from VPP 8 — should match first by price priority.
    engine.submit(vpp_id=8, side="sell", price=Decimal("51.00"), qty=Decimal("2"), sim_ts=_ts(), wall_ts=_ts())
    # VPP 5 crosses both prices, but only the non-self ask may fill.
    r = engine.submit(vpp_id=5, side="buy", price=Decimal("60"), qty=Decimal("3"), sim_ts=_ts(), wall_ts=_ts())

    assert all(t.buy_vpp_id != t.sell_vpp_id for t in r.trades), "no self-trades allowed"
    assert len(r.trades) == 1
    assert r.trades[0].sell_vpp_id == 8 and r.trades[0].price == Decimal("51.00")
    assert r.trades[0].qty == Decimal("2")
    assert engine.last_price == Decimal("51.00")  # not polluted by a wash print @52.70
    # VPP 5's own 52.70 ask stays resting; its 1-kWh buy remainder rests as a bid.
    snap = engine.snapshot(depth_levels=5)
    assert snap["asks"] == [("52.70", "2")]
    assert snap["bids"] == [("60", "1")]


def test_self_trade_only_own_liquidity_rests_no_fill(engine):
    """If the only crossing liquidity is the taker's own, nothing trades."""
    engine.submit(vpp_id=7, side="sell", price=Decimal("50"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    r = engine.submit(vpp_id=7, side="buy", price=Decimal("55"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    assert r.trades == []
    assert r.order.remaining_qty == Decimal("1")  # rests as a bid
    assert engine.last_price is None


def test_cancel_removes_order(engine):
    r = engine.submit(vpp_id=1, side="buy", price=Decimal("50"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    assert engine.cancel(r.order.order_id, sim_ts=_ts(), wall_ts=_ts()) is True
    assert engine.cancel(r.order.order_id, sim_ts=_ts(), wall_ts=_ts()) is False  # gone
    snap = engine.snapshot()
    assert snap["bids"] == []
