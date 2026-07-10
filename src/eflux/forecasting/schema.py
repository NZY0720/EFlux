"""Forecast data structures shared by forecasting callers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

HORIZONS = ("5m", "1h", "12h")
HORIZON_TIMEDELTAS = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "12h": timedelta(hours=12),
}


def _finite_float(value: float) -> float:
    value = float(value)
    if value != value:
        return 0.0
    if value == float("inf"):
        return 1.0e12
    if value == float("-inf"):
        return -1.0e12
    return value


@dataclass(frozen=True)
class ForecastPoint:
    value: float
    stderr: float | None = None
    provenance: str | None = None

    def to_dict(self) -> dict[str, float | str | None]:
        return {
            "value": _finite_float(self.value),
            "stderr": None if self.stderr is None else max(0.0, _finite_float(self.stderr)),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class TargetForecast:
    h5m: ForecastPoint
    h1h: ForecastPoint
    h12h: ForecastPoint

    def by_horizon(self, name: str) -> ForecastPoint:
        if name == "5m":
            return self.h5m
        if name == "1h":
            return self.h1h
        if name == "12h":
            return self.h12h
        raise KeyError(f"unknown forecast horizon: {name!r}")

    def to_dict(self) -> dict[str, dict[str, float | None]]:
        return {
            "5m": self.h5m.to_dict(),
            "1h": self.h1h.to_dict(),
            "12h": self.h12h.to_dict(),
        }


def _zero_target() -> TargetForecast:
    point = ForecastPoint(0.0, 0.0)
    return TargetForecast(h5m=point, h1h=point, h12h=point)


@dataclass(frozen=True)
class ForecastBundle:
    as_of: datetime
    model_version: str
    price_real: TargetForecast
    price_p2p: TargetForecast
    ghi: TargetForecast
    temp_air: TargetForecast
    wind_speed: TargetForecast

    @classmethod
    def empty(cls, as_of: datetime | None = None) -> ForecastBundle:
        timestamp = as_of or datetime(1970, 1, 1, tzinfo=UTC)
        return cls(
            as_of=timestamp,
            model_version="empty",
            price_real=_zero_target(),
            price_p2p=_zero_target(),
            ghi=_zero_target(),
            temp_air=_zero_target(),
            wind_speed=_zero_target(),
        )

    def solar_factor(self, horizon: str) -> float:
        ghi = self.ghi.by_horizon(horizon).value
        return max(0.0, min(1.5, _finite_float(ghi) / 1000.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "model_version": self.model_version,
            "price_real": self.price_real.to_dict(),
            "price_p2p": self.price_p2p.to_dict(),
            "ghi": self.ghi.to_dict(),
            "temp_air": self.temp_air.to_dict(),
            "wind_speed": self.wind_speed.to_dict(),
        }
