"""Unit tests for the in-memory event bus."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from eflux.bridge.bus import InMemoryBus
from eflux.market.events import EventKind, TickEvent
from eflux.market.products import next_delivery_interval


def _tick(n: int) -> TickEvent:
    now = datetime.now(UTC)
    product = next_delivery_interval(now)
    return TickEvent(
        kind=EventKind.TICK,
        sim_ts=now,
        wall_ts=now,
        tick_no=n,
        interval_id=product.interval_id,
        delivery_start=product.start,
        delivery_end=product.end,
    )


def test_tick_carries_grid_buy_sell_band():
    tick = _tick(1).model_copy(
        update={
            "external_price": Decimal("46.57"),
            "import_price": Decimal("48.57"),
            "export_price": Decimal("44.57"),
        }
    )

    payload = tick.model_dump(mode="json")

    assert payload["external_price"] == "46.57"
    assert payload["import_price"] == "48.57"
    assert payload["export_price"] == "44.57"


@pytest.mark.asyncio
async def test_publish_subscribe_round_trip():
    bus = InMemoryBus()

    received: list = []

    async def consume():
        async for ev in bus.subscribe():
            received.append(ev)
            if len(received) >= 2:
                break

    consumer_task = asyncio.create_task(consume())
    # Give the consumer a chance to register its queue.
    await asyncio.sleep(0.01)
    bus.publish(_tick(1))
    bus.publish(_tick(2))
    await asyncio.wait_for(consumer_task, timeout=2.0)

    assert [r.tick_no for r in received] == [1, 2]


@pytest.mark.asyncio
async def test_fanout_to_multiple_subscribers():
    bus = InMemoryBus()
    a, b = [], []

    async def consume(target):
        async for ev in bus.subscribe():
            target.append(ev)
            if len(target) >= 1:
                break

    t_a = asyncio.create_task(consume(a))
    t_b = asyncio.create_task(consume(b))
    await asyncio.sleep(0.01)
    bus.publish(_tick(7))
    await asyncio.gather(asyncio.wait_for(t_a, timeout=1.0), asyncio.wait_for(t_b, timeout=1.0))

    assert len(a) == 1 and len(b) == 1
    assert a[0].tick_no == 7 and b[0].tick_no == 7


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_noop():
    bus = InMemoryBus()
    bus.publish(_tick(1))  # should not raise
    bus.publish(_tick(2))


@pytest.mark.asyncio
async def test_full_queue_drops_oldest_then_pushes_new():
    bus = InMemoryBus(maxsize=2)
    # Subscribe but don't consume — let queue fill up.
    sub_iter = bus.subscribe().__aiter__()
    # Force the subscribe coroutine to register its queue.
    consumer_task = asyncio.create_task(sub_iter.__anext__())
    await asyncio.sleep(0.01)

    bus.publish(_tick(1))
    bus.publish(_tick(2))
    bus.publish(_tick(3))  # should knock #1 out

    # First yield: should be #2 (oldest after drop).
    first = await asyncio.wait_for(consumer_task, timeout=1.0)
    assert first.tick_no == 2
