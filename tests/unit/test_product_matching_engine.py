from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from eflux.market.delivery import OrderPurpose
from eflux.market.product_engine import ProductMatchingEngine, ProductOrderEvent, ProductTrade
from eflux.market.products import DeliveryInterval, TimeInForce

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _interval(minute: int) -> DeliveryInterval:
    start = datetime(2026, 7, 11, 12, minute, tzinfo=UTC)
    return DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )


def _submit(
    engine, interval, *, vpp=1, side="sell", price="50", qty="1", tif=TimeInForce.GOOD_TIL_GATE
):
    return engine.submit(
        interval=interval,
        vpp_id=vpp,
        side=side,
        purpose=OrderPurpose.BALANCE,
        price=Decimal(price),
        qty=Decimal(qty),
        sim_ts=NOW,
        wall_ts=NOW,
        time_in_force=tif,
    )


def test_orders_cross_only_within_the_same_delivery_product():
    engine = ProductMatchingEngine()
    first, second = _interval(5), _interval(10)
    _submit(engine, first, vpp=1, side="sell", price="40")
    result = _submit(engine, second, vpp=2, side="buy", price="100")
    assert not result.trades
    assert engine.snapshot(first.interval_id)["best_ask"] == "40"
    assert engine.snapshot(second.interval_id)["best_bid"] == "100"


def test_negative_prices_match_and_resting_price_wins():
    events = []
    engine = ProductMatchingEngine(events.append)
    interval = _interval(5)
    maker = _submit(engine, interval, vpp=1, side="sell", price="-40")
    result = _submit(engine, interval, vpp=2, side="buy", price="-30")
    assert result.trades[0].price == Decimal("-40")
    assert result.trades[0].sell_order_id == maker.order.order_id
    assert any(isinstance(event, ProductTrade) for event in events)


def test_gate_closure_rejects_new_orders_and_cancels_resting_exposure():
    events = []
    engine = ProductMatchingEngine(events.append)
    interval = _interval(5)
    result = _submit(engine, interval)
    removed = engine.close_interval(
        interval.interval_id, sim_ts=interval.gate_closure, wall_ts=interval.gate_closure
    )
    assert [order.order_id for order in removed] == [result.order.order_id]
    assert engine.snapshot(interval.interval_id)["is_closed"] is True
    assert any(
        isinstance(event, ProductOrderEvent) and event.kind == "order.cancelled" for event in events
    )
    with pytest.raises(ValueError, match="not open"):
        engine.submit(
            interval=interval,
            vpp_id=2,
            side="buy",
            purpose=OrderPurpose.BALANCE,
            price=Decimal("50"),
            qty=Decimal("1"),
            sim_ts=interval.start,
            wall_ts=interval.start,
        )


def test_ioc_never_rests_its_unfilled_remainder():
    engine = ProductMatchingEngine()
    interval = _interval(5)
    result = _submit(
        engine,
        interval,
        vpp=2,
        side="buy",
        price="50",
        qty="1",
        tif=TimeInForce.IMMEDIATE_OR_CANCEL,
    )
    assert result.order.remaining_qty == Decimal("1")
    assert not engine.open_orders_for_vpp(2)


def test_fill_or_kill_is_atomic():
    engine = ProductMatchingEngine()
    interval = _interval(5)
    _submit(engine, interval, vpp=1, side="sell", price="40", qty="0.5")
    killed = _submit(
        engine,
        interval,
        vpp=2,
        side="buy",
        price="50",
        qty="1",
        tif=TimeInForce.FILL_OR_KILL,
    )
    assert killed.killed is True
    assert not killed.trades
    assert engine.snapshot(interval.interval_id)["best_ask"] == "40"


def test_self_trade_is_skipped_but_next_counterparty_can_fill():
    engine = ProductMatchingEngine()
    interval = _interval(5)
    own = _submit(engine, interval, vpp=1, side="sell", price="30")
    other = _submit(engine, interval, vpp=2, side="sell", price="40")
    result = _submit(engine, interval, vpp=1, side="buy", price="50")
    assert result.trades[0].sell_order_id == other.order.order_id
    assert engine.get(own.order.order_id) is not None
