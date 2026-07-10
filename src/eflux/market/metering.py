"""One-second physical power integration into delivery-interval energy meters."""

from __future__ import annotations

import math
from dataclasses import dataclass

from eflux.market.products import DeliveryInterval, energy_kwh_from_average_power


@dataclass(slots=True)
class IntervalMeter:
    participant_id: int
    interval: DeliveryInterval
    renewable_generation_kwh: float = 0.0
    uncontrolled_load_kwh: float = 0.0
    integrated_duration_sec: float = 0.0

    def integrate(
        self,
        *,
        renewable_power_kw: float,
        uncontrolled_load_power_kw: float,
        duration_sec: float,
    ) -> None:
        values = (renewable_power_kw, uncontrolled_load_power_kw, duration_sec)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("meter power and duration must be finite")
        if renewable_power_kw < 0.0 or uncontrolled_load_power_kw < 0.0:
            raise ValueError("meter power must be non-negative")
        if duration_sec <= 0.0:
            raise ValueError("duration_sec must be positive")
        if self.integrated_duration_sec + duration_sec > self.interval.duration_sec + 1e-9:
            raise ValueError("meter integration exceeds delivery interval duration")
        self.renewable_generation_kwh += energy_kwh_from_average_power(
            renewable_power_kw, duration_sec
        )
        self.uncontrolled_load_kwh += energy_kwh_from_average_power(
            uncontrolled_load_power_kw, duration_sec
        )
        self.integrated_duration_sec += duration_sec


class IntervalMeterBook:
    def __init__(self) -> None:
        self._meters: dict[tuple[int, str], IntervalMeter] = {}

    def integrate(
        self,
        *,
        participant_id: int,
        interval: DeliveryInterval,
        renewable_power_kw: float,
        uncontrolled_load_power_kw: float,
        duration_sec: float,
    ) -> IntervalMeter:
        key = (participant_id, interval.interval_id)
        meter = self._meters.setdefault(key, IntervalMeter(participant_id, interval))
        if meter.interval != interval:
            raise ValueError(f"conflicting definition for interval {interval.interval_id}")
        meter.integrate(
            renewable_power_kw=renewable_power_kw,
            uncontrolled_load_power_kw=uncontrolled_load_power_kw,
            duration_sec=duration_sec,
        )
        return meter

    def get(self, participant_id: int, interval_id: str) -> IntervalMeter | None:
        return self._meters.get((participant_id, interval_id))

    def pop(self, participant_id: int, interval_id: str) -> IntervalMeter | None:
        return self._meters.pop((participant_id, interval_id), None)
