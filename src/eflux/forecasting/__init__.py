"""Lightweight online forecasting primitives."""

from __future__ import annotations

from eflux.forecasting.schema import ForecastBundle, ForecastPoint, TargetForecast
from eflux.forecasting.service import MODEL_VERSION, ForecastService

__all__ = [
    "MODEL_VERSION",
    "ForecastBundle",
    "ForecastPoint",
    "ForecastService",
    "TargetForecast",
]
