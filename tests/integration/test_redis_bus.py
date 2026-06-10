"""RedisStreamBus round-trip test.

Skipped automatically when no Redis is reachable at REDIS_URL (or default localhost:6379).
Run locally with: brew install redis && brew services start redis
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest

from eflux.market.events import EventKind, TickEvent

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


async def _redis_available() -> bool:
    try:
        import redis.asyncio as aioredis
    except ImportError:
        return False
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
        return True
    except Exception:
        return False
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_publish_subscribe_round_trip():
    if not await _redis_available():
        pytest.skip(f"Redis unreachable at {REDIS_URL}")

    from eflux.bridge.redis_bus import RedisStreamBus

    # Use a unique stream key so concurrent test runs don't collide.
    stream_key = f"stream:test:{os.getpid()}:{int(datetime.now().timestamp() * 1000)}"
    producer = RedisStreamBus(REDIS_URL, stream_key=stream_key)
    consumer = RedisStreamBus(REDIS_URL, stream_key=stream_key)

    received: list = []

    async def consume():
        async for ev in consumer.subscribe():
            received.append(ev)
            if len(received) >= 2:
                break

    consumer_task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # let XREAD register

    now = datetime.now(UTC)
    producer.publish(TickEvent(kind=EventKind.TICK, sim_ts=now, wall_ts=now, tick_no=1))
    producer.publish(TickEvent(kind=EventKind.TICK, sim_ts=now, wall_ts=now, tick_no=2))
    # Give the fire-and-forget XADD tasks time to land.
    await asyncio.sleep(0.5)

    await asyncio.wait_for(consumer_task, timeout=10.0)
    assert [r.tick_no for r in received] == [1, 2]

    await producer.close()
    await consumer.close()


@pytest.mark.asyncio
async def test_lifespan_falls_back_when_redis_down(monkeypatch, db_session):
    """If EFLUX_BUS_BACKEND=redis but the URL doesn't resolve, lifespan should pick InMemoryBus."""
    monkeypatch.setenv("EFLUX_BUS_BACKEND", "redis")
    monkeypatch.setenv("EFLUX_REDIS_URL", "redis://127.0.0.1:1/0")  # bogus port — guaranteed refused

    from eflux.bridge.bus import InMemoryBus
    from eflux.config import get_settings
    from eflux.db.session import get_engine, get_sessionmaker
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    from eflux.api.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.bus, InMemoryBus)
