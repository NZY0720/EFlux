"""Redis Stream-backed event bus.

Producer: XADD to stream key. Consumer: XREAD from $ (live tail). The stream is also a
durable replay log — bounded with MAXLEN to keep memory in check.

Requires Redis running. Selected via `EFLUX_BUS_BACKEND=redis`. On startup the app
pings Redis and falls back to InMemoryBus if it's unreachable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from decimal import Decimal

import redis.asyncio as aioredis

from eflux.bridge.bus import EventBus
from eflux.market.events import (
    EventKind,
    ExternalTradeEvent,
    MarketEvent,
    OrderEvent,
    TickEvent,
    TradeEvent,
)

log = logging.getLogger(__name__)

STREAM_KEY = "stream:market"
MAXLEN = 100_000


def _serialize(ev: MarketEvent) -> dict[str, str]:
    return {"json": json.dumps(ev.model_dump(mode="json"), default=str)}


def _deserialize(payload: dict[str, str]) -> MarketEvent:
    data = json.loads(payload["json"])
    kind = data.get("kind")
    if kind == EventKind.TRADE.value:
        return TradeEvent.model_validate(_decimalize(data, ["price", "qty"]))
    if kind == EventKind.EXTERNAL_TRADE.value:
        return ExternalTradeEvent.model_validate(_decimalize(data, ["price", "raw_lmp", "qty"]))
    if kind == EventKind.TICK.value:
        return TickEvent.model_validate(
            _decimalize(
                data,
                ["best_bid", "best_ask", "last_price", "external_price", "bid_depth", "ask_depth"],
            )
        )
    return OrderEvent.model_validate(_decimalize(data, ["price", "qty", "remaining_qty"]))


def _decimalize(d: dict, keys: list[str]) -> dict:
    for k in keys:
        if d.get(k) is not None:
            d[k] = Decimal(str(d[k]))
    return d


class RedisStreamBus(EventBus):
    def __init__(self, redis_url: str, *, stream_key: str = STREAM_KEY, maxlen: int = MAXLEN) -> None:
        # The client is created lazily so the first await runs inside the
        # caller's running loop (avoids "attached to a different loop" errors
        # when the bus is constructed before the asyncio loop fully starts).
        self._client = aioredis.from_url(redis_url, decode_responses=True)
        self._stream_key = stream_key
        self._maxlen = maxlen
        self._publish_tasks: set[asyncio.Task[None]] = set()
        self._publish_tail: asyncio.Task[None] | None = None

    async def ping(self) -> None:
        """Round-trip a PING so the lifespan can decide whether Redis is up."""
        await self._client.ping()

    def publish(self, event: MarketEvent) -> None:
        # The matching engine is sync — we schedule the XADD onto the current loop.
        # If no loop is running we cannot block here (would deadlock the engine),
        # so we drop the event and log. This only happens in pathological setups
        # (e.g. someone publishing from a thread with no event loop attached).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("RedisStreamBus.publish called with no running loop — event dropped: %s", event.kind)
            return
        # XADD is async but the stream must preserve publish order (clients
        # backfill the tape/chart from it), so each publish awaits the previous
        # one before writing. The chain is self-trimming: finished tasks remove
        # themselves from the set, and each link drops its `previous` reference
        # once it proceeds. Trade-off: a stalled Redis stalls the whole chain
        # (head-of-line blocking) rather than dropping or reordering events.
        previous = self._publish_tail

        async def publish_after_previous() -> None:
            if previous is not None:
                try:
                    await previous
                except Exception:
                    # The previous link already logged its own XADD failure; we
                    # only awaited it for ordering, so don't break the chain.
                    log.debug("prior publish failed before %s; continuing chain", event.kind)
            await self._publish_async(event)

        task = loop.create_task(publish_after_previous())
        self._publish_tail = task
        self._publish_tasks.add(task)
        task.add_done_callback(self._publish_tasks.discard)

    async def _publish_async(self, event: MarketEvent) -> None:
        try:
            await self._client.xadd(
                self._stream_key, _serialize(event), maxlen=self._maxlen, approximate=True
            )
        except Exception:
            log.exception("Redis XADD failed for event %s", event.kind)

    async def subscribe(self) -> AsyncIterator[MarketEvent]:
        last_id = "$"  # only new messages
        while True:
            resp = await self._client.xread({self._stream_key: last_id}, block=5000, count=100)
            if not resp:
                continue
            for _, entries in resp:
                for entry_id, fields in entries:
                    last_id = entry_id
                    try:
                        yield _deserialize(fields)
                    except Exception:
                        continue

    async def close(self) -> None:
        # Drain in-flight publishes so the tail of the stream isn't lost when the
        # app shuts down between an XADD being scheduled and awaited.
        if self._publish_tasks:
            await asyncio.gather(*self._publish_tasks, return_exceptions=True)
        self._publish_tail = None
        await self._client.aclose()
