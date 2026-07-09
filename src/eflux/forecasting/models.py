"""Numpy-only online forecasting models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import cos, pi, sin, sqrt
from typing import Any

import numpy as np

from eflux.forecasting.schema import ForecastPoint, HORIZONS, HORIZON_TIMEDELTAS

FEATURE_DIM = 8
DEFAULT_MAX_OBSERVATIONS = 3 * 24 * 60


def _clean_float(value: float, default: float = 0.0) -> float:
    value = float(value)
    if np.isfinite(value):
        return value
    return default


def _clamp(value: float, bounds: tuple[float, float] | None) -> float:
    value = _clean_float(value)
    if bounds is None:
        return value
    lo, hi = bounds
    return float(np.clip(value, lo, hi))


def _hour_features(ts: datetime) -> list[float]:
    hour = ts.hour + ts.minute / 60.0 + ts.second / 3600.0
    phase = 2.0 * pi * hour / 24.0
    return [sin(phase), cos(phase), sin(2.0 * phase), cos(2.0 * phase)]


def _iso(ts: datetime) -> str:
    return ts.isoformat()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class OnlineLinearForecaster:
    """Recursive least-squares forecaster with exponential forgetting."""

    n_features: int = FEATURE_DIM
    forgetting_factor: float = 0.999
    output_bounds: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        self.coef = np.zeros(self.n_features, dtype=float)
        self.P = np.eye(self.n_features, dtype=float) * 1.0e3
        self.n_updates = 0
        self.residual_var = 1.0

    def predict(self, features: np.ndarray | list[float]) -> tuple[float, float]:
        x = self._features(features)
        mean = _clamp(float(x @ self.coef), self.output_bounds)
        leverage = max(0.0, float(x @ self.P @ x))
        variance = max(1.0e-9, self.residual_var + leverage)
        return mean, min(1.0e6, sqrt(variance))

    def update(self, features: np.ndarray | list[float], target: float) -> None:
        y = _clean_float(target)
        x = self._features(features)
        prediction = float(x @ self.coef)
        error = y - prediction

        p_x = self.P @ x
        denom = self.forgetting_factor + float(x @ p_x)
        if not np.isfinite(denom) or denom <= 1.0e-12:
            return

        gain = p_x / denom
        self.coef = self.coef + gain * error
        self.P = (self.P - np.outer(gain, x @ self.P)) / self.forgetting_factor
        self.P = 0.5 * (self.P + self.P.T)
        self.P = np.nan_to_num(self.P, nan=0.0, posinf=1.0e6, neginf=-1.0e6)
        self.P = np.clip(self.P, -1.0e6, 1.0e6)

        alpha = 0.03
        err2 = min(1.0e12, error * error)
        self.residual_var = (1.0 - alpha) * self.residual_var + alpha * err2
        self.n_updates += 1

    def _features(self, features: np.ndarray | list[float]) -> np.ndarray:
        x = np.asarray(features, dtype=float)
        if x.shape != (self.n_features,):
            raise ValueError(f"expected {self.n_features} features, got shape {x.shape}")
        return np.nan_to_num(x, nan=0.0, posinf=1.0e6, neginf=-1.0e6)

    def to_state(self) -> dict[str, Any]:
        return {
            "n_features": self.n_features,
            "forgetting_factor": self.forgetting_factor,
            "output_bounds": self.output_bounds,
            "coef": self.coef.tolist(),
            "P": self.P.tolist(),
            "n_updates": self.n_updates,
            "residual_var": self.residual_var,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "OnlineLinearForecaster":
        model = cls(
            n_features=int(state["n_features"]),
            forgetting_factor=float(state["forgetting_factor"]),
            output_bounds=tuple(state["output_bounds"]) if state["output_bounds"] is not None else None,
        )
        model.coef = np.asarray(state["coef"], dtype=float)
        model.P = np.asarray(state["P"], dtype=float)
        model.n_updates = int(state["n_updates"])
        model.residual_var = float(state["residual_var"])
        return model


class SeasonalPersistence:
    """Last value for the same hour-of-day, with plain persistence fallback."""

    def __init__(self, default: float = 0.0) -> None:
        self.default = float(default)
        self.last_value: float | None = None
        self.by_hour: dict[int, float] = {}
        self.n_observations = 0

    def observe(self, ts: datetime, value: float) -> None:
        clean = _clean_float(value, self.default)
        self.last_value = clean
        self.by_hour[int(ts.hour)] = clean
        self.n_observations += 1

    def predict(self, ts: datetime) -> float:
        if int(ts.hour) in self.by_hour:
            return self.by_hour[int(ts.hour)]
        if self.last_value is not None:
            return self.last_value
        return self.default

    def to_state(self) -> dict[str, Any]:
        return {
            "default": self.default,
            "last_value": self.last_value,
            "by_hour": {str(k): v for k, v in self.by_hour.items()},
            "n_observations": self.n_observations,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "SeasonalPersistence":
        model = cls(default=float(state["default"]))
        model.last_value = None if state["last_value"] is None else float(state["last_value"])
        model.by_hour = {int(k): float(v) for k, v in state["by_hour"].items()}
        model.n_observations = int(state["n_observations"])
        return model


class HorizonModel:
    """Three-horizon online model with seasonal persistence fallback."""

    def __init__(
        self,
        *,
        output_bounds: tuple[float, float] | None = None,
        forgetting_factor: float = 0.999,
        max_observations: int = DEFAULT_MAX_OBSERVATIONS,
    ) -> None:
        self.output_bounds = output_bounds
        self.max_observations = max(int(max_observations), DEFAULT_MAX_OBSERVATIONS)
        self.linear = {
            horizon: OnlineLinearForecaster(
                forgetting_factor=forgetting_factor,
                output_bounds=output_bounds,
            )
            for horizon in HORIZONS
        }
        self.seasonal = SeasonalPersistence()
        self.observations: list[tuple[datetime, float]] = []

    def predict(self, sim_ts: datetime) -> dict[str, ForecastPoint]:
        result: dict[str, ForecastPoint] = {}
        for horizon in HORIZONS:
            delta = HORIZON_TIMEDELTAS[horizon]
            features = self._features(sim_ts, delta)
            linear_mean, linear_stderr = self.linear[horizon].predict(features)
            target_ts = sim_ts + delta
            seasonal_mean = self.seasonal.predict(target_ts)
            weight = self._linear_weight(self.linear[horizon].n_updates)
            mean = weight * linear_mean + (1.0 - weight) * seasonal_mean
            mean = _clamp(mean, self.output_bounds)
            stderr = linear_stderr if self.linear[horizon].n_updates else 0.0
            result[horizon] = ForecastPoint(mean, stderr)
        return result

    def observe(self, sim_ts: datetime, value: float) -> None:
        clean = _clamp(value, self.output_bounds)
        for horizon in HORIZONS:
            delta = HORIZON_TIMEDELTAS[horizon]
            origin_ts = sim_ts - delta
            if self._has_observation_at_or_before(origin_ts):
                self.linear[horizon].update(self._features(origin_ts, delta), clean)
        self._append_observation(sim_ts, clean)
        self.seasonal.observe(sim_ts, clean)

    def _linear_weight(self, n_updates: int) -> float:
        return min(0.85, n_updates / (n_updates + 24.0)) if n_updates > 0 else 0.0

    def _append_observation(self, ts: datetime, value: float) -> None:
        self.observations.append((ts, value))
        if len(self.observations) >= 2 and self.observations[-2][0] > ts:
            self.observations.sort(key=lambda item: item[0])
        if len(self.observations) > self.max_observations:
            self.observations = self.observations[-self.max_observations :]

    def _has_observation_at_or_before(self, ts: datetime) -> bool:
        return self._value_at_or_before(ts) is not None

    def _features(self, origin_ts: datetime, horizon_delta: timedelta) -> np.ndarray:
        target_ts = origin_ts + horizon_delta
        last = self._value_at_or_before(origin_ts)
        last_value = 0.0 if last is None else last[1]
        lag_delta, trend_delta = self._lookback_windows(horizon_delta)
        lag = self._value_at_or_before(origin_ts - lag_delta)
        lag_value = last_value if lag is None else lag[1]
        recent = self._value_at_or_before(origin_ts - trend_delta)
        recent_value = lag_value if recent is None else recent[1]
        trend = last_value - recent_value
        return np.asarray([1.0, *_hour_features(target_ts), last_value, lag_value, trend], dtype=float)

    def _lookback_windows(self, horizon_delta: timedelta) -> tuple[timedelta, timedelta]:
        if horizon_delta <= timedelta(minutes=15):
            return horizon_delta, timedelta(minutes=15)
        if horizon_delta <= timedelta(hours=1):
            return horizon_delta, timedelta(hours=2)
        return timedelta(hours=24), timedelta(hours=12)

    def _value_at_or_before(self, ts: datetime) -> tuple[datetime, float] | None:
        for obs_ts, value in reversed(self.observations):
            if obs_ts <= ts:
                return obs_ts, value
        return None

    def to_state(self) -> dict[str, Any]:
        return {
            "output_bounds": self.output_bounds,
            "max_observations": self.max_observations,
            "linear": {horizon: model.to_state() for horizon, model in self.linear.items()},
            "seasonal": self.seasonal.to_state(),
            "observations": [[_iso(ts), value] for ts, value in self.observations],
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "HorizonModel":
        model = cls(
            output_bounds=tuple(state["output_bounds"]) if state["output_bounds"] is not None else None,
            max_observations=int(state["max_observations"]),
        )
        model.linear = {
            horizon: OnlineLinearForecaster.from_state(model_state)
            for horizon, model_state in state["linear"].items()
        }
        model.seasonal = SeasonalPersistence.from_state(state["seasonal"])
        model.observations = [(_parse_ts(ts), float(value)) for ts, value in state["observations"]]
        return model


class WeatherForecaster(HorizonModel):
    """Weather forecaster with optional injected NWP base forecasts."""

    def __init__(
        self,
        *,
        nwp_lookup: Callable[[datetime], float] | None = None,
        output_bounds: tuple[float, float] | None = None,
        forgetting_factor: float = 0.999,
        max_observations: int = 4096,
        residual_alpha: float = 0.2,
    ) -> None:
        super().__init__(
            output_bounds=output_bounds,
            forgetting_factor=forgetting_factor,
            max_observations=max_observations,
        )
        self.nwp_lookup = nwp_lookup
        self.residual_alpha = float(residual_alpha)
        self.residual_ewma = 0.0
        self.residual_var = 1.0
        self.n_residuals = 0

    def predict(self, sim_ts: datetime) -> dict[str, ForecastPoint]:
        if self.nwp_lookup is None:
            return super().predict(sim_ts)

        result: dict[str, ForecastPoint] = {}
        for horizon in HORIZONS:
            delta = HORIZON_TIMEDELTAS[horizon]
            target_ts = sim_ts + delta
            if horizon == "5m":
                base = self.seasonal.last_value if self.seasonal.last_value is not None else self.seasonal.predict(target_ts)
                stderr = 0.0
            else:
                base = _clean_float(self.nwp_lookup(target_ts))
                stderr = sqrt(max(1.0e-9, self.residual_var)) if self.n_residuals else 0.0
            result[horizon] = ForecastPoint(_clamp(base + self.residual_ewma, self.output_bounds), stderr)
        return result

    def observe(self, sim_ts: datetime, value: float) -> None:
        clean = _clamp(value, self.output_bounds)
        if self.nwp_lookup is not None:
            residual = clean - _clean_float(self.nwp_lookup(sim_ts))
            if self.n_residuals == 0:
                self.residual_ewma = residual
                self.residual_var = 0.0
            else:
                error = residual - self.residual_ewma
                self.residual_ewma = (1.0 - self.residual_alpha) * self.residual_ewma + self.residual_alpha * residual
                self.residual_var = (1.0 - self.residual_alpha) * self.residual_var + self.residual_alpha * error * error
            self.n_residuals += 1
        super().observe(sim_ts, clean)

    def to_state(self) -> dict[str, Any]:
        state = super().to_state()
        state.update(
            {
                "residual_alpha": self.residual_alpha,
                "residual_ewma": self.residual_ewma,
                "residual_var": self.residual_var,
                "n_residuals": self.n_residuals,
            }
        )
        return state

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any],
        *,
        nwp_lookup: Callable[[datetime], float] | None = None,
    ) -> "WeatherForecaster":
        model = cls(
            nwp_lookup=nwp_lookup,
            output_bounds=tuple(state["output_bounds"]) if state["output_bounds"] is not None else None,
            max_observations=int(state["max_observations"]),
            residual_alpha=float(state["residual_alpha"]),
        )
        model.linear = {
            horizon: OnlineLinearForecaster.from_state(model_state)
            for horizon, model_state in state["linear"].items()
        }
        model.seasonal = SeasonalPersistence.from_state(state["seasonal"])
        model.observations = [(_parse_ts(ts), float(value)) for ts, value in state["observations"]]
        model.residual_ewma = float(state["residual_ewma"])
        model.residual_var = float(state["residual_var"])
        model.n_residuals = int(state["n_residuals"])
        return model
