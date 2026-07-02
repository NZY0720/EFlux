"""Shared per-account rate limiting (token buckets).

Extracted from the Agent Protocol batch endpoint so every governed surface (order
batches, guidance ingestion, ...) draws one implementation. In-memory and per-process
by design — consistent with the ephemeral market state; buckets reset on restart.
"""

from __future__ import annotations

import time


class TokenBucket:
    __slots__ = ("capacity", "last", "refill_per_sec", "tokens")

    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.tokens = float(capacity)
        self.last = time.monotonic()

    def take(self, n: float) -> tuple[bool, int]:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now
        if self.tokens >= n:
            self.tokens -= n
            return True, int(self.tokens)
        return False, int(self.tokens)


_REGISTRY: list[RateLimiter] = []


class RateLimiter:
    """Keyed token buckets — one bucket per account/API principal."""

    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._buckets: dict[object, TokenBucket] = {}
        _REGISTRY.append(self)

    def check(self, key: object, cost: float) -> tuple[bool, int]:
        """Try to spend ``cost`` tokens for ``key``. Returns (allowed, tokens_remaining)."""
        bucket = self._buckets.get(key) or self._buckets.setdefault(
            key, TokenBucket(self.capacity, self.refill_per_sec)
        )
        return bucket.take(cost)

    def reset(self) -> None:
        self._buckets.clear()


def reset_all_limiters() -> None:
    """Test hook: clear every limiter's buckets (user ids restart with each fresh DB)."""
    for limiter in _REGISTRY:
        limiter.reset()
