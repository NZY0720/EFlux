"""Event bus abstraction. Two backends: in-memory (default) and Redis Streams.

Producer-side: publish(event). Sync, fire-and-forget.
Consumer-side: subscribe() returns an async iterator over events.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from eflux.market.events import MarketEvent


class EventBus(ABC):
    @abstractmethod
    def publish(self, event: MarketEvent) -> None: ...

    @abstractmethod
    async def subscribe(self) -> AsyncIterator[MarketEvent]: ...

    async def close(self) -> None:
        return None


class InMemoryBus(EventBus):
    """Simple fan-out broker. Each subscriber gets its own bounded queue."""

    def __init__(self, maxsize: int = 1024) -> None:
        self._subs: list[asyncio.Queue[MarketEvent]] = []
        self._maxsize = maxsize
        self._lock = asyncio.Lock()

    def publish(self, event: MarketEvent) -> None:
        # Sync caller (matching engine) — drop on full to keep producer non-blocking.
        for q in self._subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow subscriber. Drop oldest, push newest.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def subscribe(self) -> AsyncIterator[MarketEvent]:
        q: asyncio.Queue[MarketEvent] = asyncio.Queue(maxsize=self._maxsize)
        async with self._lock:
            self._subs.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            async with self._lock:
                if q in self._subs:
                    self._subs.remove(q)
