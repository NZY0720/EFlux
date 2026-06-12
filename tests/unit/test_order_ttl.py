"""Unit tests for resting-order TTL expiry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


def test_order_without_ttl_never_expires(engine):
    engine.submit(vpp_id=1, side="sell", price=Decimal("60"), qty=Decimal("1"), sim_ts=_ts(), wall_ts=_ts())
    removed = engine.expire(sim_ts=_ts() + timedelta(days=365), wall_ts=_ts())
    assert removed == []
    assert engine.book.best_ask() is not None


def test_order_survives_until_ttl(engine):
    t0 = _ts()
    engine.submit(vpp_id=1, side="sell", price=Decimal("60"), qty=Decimal("1"), sim_ts=t0, wall_ts=t0, ttl_sec=10)
    assert engine.expire(sim_ts=t0 + timedelta(seconds=9), wall_ts=_ts()) == []
    assert engine.book.best_ask() is not None


def test_expired_order_removed_and_cancel_event_published(engine, published):
    t0 = _ts()
    r = engine.submit(
        vpp_id=1, side="sell", price=Decimal("60"), qty=Decimal("1"), sim_ts=t0, wall_ts=t0, ttl_sec=10
    )
    published.clear()

    removed = engine.expire(sim_ts=t0 + timedelta(seconds=10), wall_ts=_ts())

    assert [o.order_id for o in removed] == [r.order.order_id]
    assert engine.book.best_ask() is None
    assert len(published) == 1
    assert published[0].kind == EventKind.ORDER_CANCELLED.value
    assert published[0].order_id == r.order.order_id


def test_expiry_sweeps_both_sides(engine):
    t0 = _ts()
    engine.submit(vpp_id=1, side="sell", price=Decimal("60"), qty=Decimal("1"), sim_ts=t0, wall_ts=t0, ttl_sec=5)
    engine.submit(vpp_id=2, side="buy", price=Decimal("40"), qty=Decimal("1"), sim_ts=t0, wall_ts=t0, ttl_sec=5)
    removed = engine.expire(sim_ts=t0 + timedelta(seconds=6), wall_ts=_ts())
    assert {o.side for o in removed} == {"buy", "sell"}
    assert engine.book.best_ask() is None and engine.book.best_bid() is None


def test_partially_filled_order_expires_with_remainder(engine):
    t0 = _ts()
    engine.submit(vpp_id=1, side="sell", price=Decimal("50"), qty=Decimal("2"), sim_ts=t0, wall_ts=t0, ttl_sec=10)
    engine.submit(vpp_id=2, side="buy", price=Decimal("55"), qty=Decimal("0.5"), sim_ts=t0, wall_ts=t0)

    removed = engine.expire(sim_ts=t0 + timedelta(seconds=11), wall_ts=_ts())

    assert len(removed) == 1
    assert removed[0].remaining_qty == Decimal("1.5")  # only the unfilled remainder


def test_dispatched_flag_survives_expiry(engine):
    t0 = _ts()
    engine.submit(
        vpp_id=1, side="sell", price=Decimal("60"), qty=Decimal("1"),
        sim_ts=t0, wall_ts=t0, ttl_sec=10, dispatched=True,
    )
    removed = engine.expire(sim_ts=t0 + timedelta(seconds=11), wall_ts=_ts())
    assert removed[0].dispatched is True
