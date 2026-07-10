"""Delivery products and power/energy conversions for market V2.

The market trades *terminal energy* (kWh at the VPP point of common coupling)
for an explicit delivery interval.  Power (kW) is a rate; it only becomes an
energy quantity after multiplying by a duration.  Keeping that distinction in
one small module prevents order sizing, resource reservation, and settlement
from silently using different time bases.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

DEFAULT_DELIVERY_INTERVAL_SEC = 5 * 60
DEFAULT_TRADING_HORIZON_SEC = 30 * 60


def energy_kwh_from_average_power(power_kw: float, duration_sec: float) -> float:
    """Convert signed average power over a duration to signed terminal energy."""

    if not math.isfinite(power_kw):
        raise ValueError("power_kw must be finite")
    if not math.isfinite(duration_sec) or duration_sec <= 0.0:
        raise ValueError("duration_sec must be finite and positive")
    return power_kw * duration_sec / 3600.0


def average_power_kw_from_energy(energy_kwh: float, duration_sec: float) -> float:
    """Convert signed terminal energy to the average power required to deliver it."""

    if not math.isfinite(energy_kwh):
        raise ValueError("energy_kwh must be finite")
    if not math.isfinite(duration_sec) or duration_sec <= 0.0:
        raise ValueError("duration_sec must be finite and positive")
    return energy_kwh * 3600.0 / duration_sec


def _utc(ts: datetime, field: str) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return ts.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DeliveryInterval:
    """One fungible energy product with a fixed physical delivery window.

    Orders may enter only in ``[opens_at, gate_closure)``.  Gate closure is no
    later than delivery start, so a filled order always reserves a complete
    delivery window and never implies impossible instantaneous catch-up power.
    """

    market: str
    start: datetime
    end: datetime
    gate_closure: datetime
    opens_at: datetime

    def __post_init__(self) -> None:
        market = self.market.strip().lower()
        if not market:
            raise ValueError("market must be non-empty")
        start = _utc(self.start, "start")
        end = _utc(self.end, "end")
        gate = _utc(self.gate_closure, "gate_closure")
        opens = _utc(self.opens_at, "opens_at")
        if end <= start:
            raise ValueError("delivery interval end must be after start")
        if gate > start:
            raise ValueError("gate_closure must be no later than delivery start")
        if opens > gate:
            raise ValueError("opens_at must be no later than gate_closure")
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "gate_closure", gate)
        object.__setattr__(self, "opens_at", opens)

    @property
    def interval_id(self) -> str:
        return f"{self.market}:{self.start.isoformat()}:{self.end.isoformat()}"

    @property
    def duration_sec(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def duration_h(self) -> float:
        return self.duration_sec / 3600.0

    def is_trading_open(self, at: datetime) -> bool:
        at = _utc(at, "at")
        return self.opens_at <= at < self.gate_closure

    def is_delivering(self, at: datetime) -> bool:
        at = _utc(at, "at")
        return self.start <= at < self.end

    def is_settleable(self, at: datetime) -> bool:
        return _utc(at, "at") >= self.end


def next_delivery_interval(
    at: datetime,
    *,
    market: str = "p2p",
    interval_sec: int = DEFAULT_DELIVERY_INTERVAL_SEC,
    lead_intervals: int = 1,
    trading_horizon_sec: int = DEFAULT_TRADING_HORIZON_SEC,
) -> DeliveryInterval:
    """Return the aligned upcoming delivery product visible at ``at``.

    With the V2 default ``lead_intervals=1``, the currently-delivering interval
    is never traded.  Orders target the next full five-minute window and close
    exactly at its start.
    """

    at_utc = _utc(at, "at")
    if interval_sec <= 0:
        raise ValueError("interval_sec must be positive")
    if lead_intervals < 1:
        raise ValueError("lead_intervals must be at least 1")
    if trading_horizon_sec < 0:
        raise ValueError("trading_horizon_sec must be non-negative")
    epoch_sec = int(at_utc.timestamp())
    current_start_sec = epoch_sec - epoch_sec % interval_sec
    start = datetime.fromtimestamp(current_start_sec + lead_intervals * interval_sec, tz=UTC)
    end = start + timedelta(seconds=interval_sec)
    return DeliveryInterval(
        market=market,
        start=start,
        end=end,
        gate_closure=start,
        opens_at=start - timedelta(seconds=trading_horizon_sec),
    )
