"""Shared per-account rate limiting (token buckets).

Extracted from the Agent Protocol batch endpoint so every governed surface (order
batches, guidance ingestion, ...) draws one implementation. In-memory and per-process
by design — consistent with the ephemeral market state; buckets reset on restart.
"""

from __future__ import annotations

import time
from collections import OrderedDict


class TokenBucket:
    __slots__ = ("capacity", "last", "refill_per_sec", "tokens")

    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.tokens = float(capacity)
        self.last = time.monotonic()

    def refill(self, now: float) -> None:
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now

    def take(self, n: float, *, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        self.refill(now)
        if self.tokens >= n:
            self.tokens -= n
            return True, int(self.tokens)
        return False, int(self.tokens)

    def idle_refilled(self, now: float) -> bool:
        if self.refill_per_sec <= 0:
            return False
        idle_sec = now - self.last
        full_refill_sec = self.capacity / self.refill_per_sec
        would_refill = self.tokens + idle_sec * self.refill_per_sec >= self.capacity
        return idle_sec >= full_refill_sec and would_refill


_REGISTRY: list[RateLimiter] = []


class RateLimiter:
    """Keyed token buckets — one bucket per account/API principal."""

    def __init__(self, capacity: float, refill_per_sec: float, *, sweep_threshold: int = 1024) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._buckets: OrderedDict[object, TokenBucket] = OrderedDict()
        self._sweep_threshold = sweep_threshold
        _REGISTRY.append(self)

    def check(self, key: object, cost: float) -> tuple[bool, int]:
        """Try to spend ``cost`` tokens for ``key``. Returns (allowed, tokens_remaining)."""
        now = time.monotonic()
        if len(self._buckets) > self._sweep_threshold:
            self._sweep(now)
        bucket = self._buckets.get(key) or self._buckets.setdefault(
            key, TokenBucket(self.capacity, self.refill_per_sec)
        )
        self._buckets.move_to_end(key)
        return bucket.take(cost, now=now)

    def _sweep(self, now: float) -> None:
        while self._buckets:
            key, bucket = next(iter(self._buckets.items()))
            if not bucket.idle_refilled(now):
                break
            self._buckets.pop(key, None)

    def reset(self) -> None:
        self._buckets.clear()


def reset_all_limiters() -> None:
    """Test hook: clear every limiter's buckets (user ids restart with each fresh DB)."""
    for limiter in _REGISTRY:
        limiter.reset()
