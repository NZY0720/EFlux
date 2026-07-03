"""Unit tests for the shared API token-bucket limiter."""

from __future__ import annotations

from eflux.api import ratelimit
from eflux.api.ratelimit import RateLimiter


def test_rate_limiter_evicts_idle_refilled_buckets(monkeypatch):
    now = 0.0
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now)
    limiter = RateLimiter(capacity=10, refill_per_sec=1, sweep_threshold=0)

    assert limiter.check("stale", 5) == (True, 5)
    now = 5.0
    assert limiter.check("active", 1) == (True, 9)

    now = 11.0
    assert limiter.check("new", 1) == (True, 9)
    assert "stale" not in limiter._buckets
    assert "active" in limiter._buckets

    assert limiter.check("active", 1) == (True, 9)
