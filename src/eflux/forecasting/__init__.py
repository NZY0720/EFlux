"""Lightweight online forecasting primitives."""

from __future__ import annotations

from eflux.forecasting.schema import ForecastBundle, ForecastPoint, TargetForecast
from eflux.forecasting.service import ForecastService, MODEL_VERSION

__all__ = [
    "ForecastBundle",
    "ForecastPoint",
    "ForecastService",
    "MODEL_VERSION",
    "TargetForecast",
]
