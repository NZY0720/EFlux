"""Numpy-only online forecasting models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import cos, exp, pi, sin, sqrt
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from eflux.forecasting.schema import HORIZON_TIMEDELTAS, HORIZONS, ForecastPoint

# v1 feature set: [bias, 4 hour-of-day harmonics, 2 day-of-week harmonics,
# last, daily/horizon lag, trend, 15-min ramp]. Persisted states with the old
# 8-dim linear models are upgraded on load by replaying their observations.
FEATURE_DIM = 11
DEFAULT_MAX_OBSERVATIONS = 3 * 24 * 60
MARKET_TIMEZONE = ZoneInfo("America/Los_Angeles")


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


def _market_time(ts: datetime) -> datetime:
    """Canonical timestamp for all calendar features and persisted replay."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=MARKET_TIMEZONE)
    return ts.astimezone(MARKET_TIMEZONE)


def _hour_features(ts: datetime) -> list[float]:
    ts = _market_time(ts)
    hour = ts.hour + ts.minute / 60.0 + ts.second / 3600.0
    phase = 2.0 * pi * hour / 24.0
    return [sin(phase), cos(phase), sin(2.0 * phase), cos(2.0 * phase)]


def _dow_features(ts: datetime) -> list[float]:
    ts = _market_time(ts)
    day = ts.weekday() + (ts.hour + ts.minute / 60.0) / 24.0
    phase = 2.0 * pi * day / 7.0
    return [sin(phase), cos(phase)]


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
    def from_state(cls, state: dict[str, Any]) -> OnlineLinearForecaster:
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


@dataclass(frozen=True)
class AnchorForecast:
    """Forward-curve value plus its coverage/provenance state.

    ``value=None`` means the requested target hour is not covered. Callers must
    use the explicit persistence/seasonal fallback instead of inventing a fixed
    price anchor.
    """

    value: float | None
    provenance: str
    source_id: str | None = None


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
    def from_state(cls, state: dict[str, Any]) -> SeasonalPersistence:
        model = cls(default=float(state["default"]))
        model.last_value = None if state["last_value"] is None else float(state["last_value"])
        model.by_hour = {int(k): float(v) for k, v in state["by_hour"].items()}
        model.n_observations = int(state["n_observations"])
        return model


class HourlyEwmaProfile:
    """Per-hour-of-day EWMA profile of observed values (market timezone).

    Own-market price anchor: a bucket stays unavailable until it has seen
    ``min_obs`` observations, so a cold profile yields an explicit degraded
    forecast instead of a guessed constant."""

    def __init__(self, alpha: float = 0.05, min_obs: int = 5) -> None:
        self.alpha = min(1.0, max(1.0e-6, float(alpha)))
        self.min_obs = max(1, int(min_obs))
        self.by_hour: dict[int, float] = {}
        self.counts: dict[int, int] = {}
        self.prior_hours: set[int] = set()

    def observe(self, ts: datetime, value: float) -> None:
        clean = _clean_float(value)
        hour = _market_time(ts).hour
        if hour in self.prior_hours:
            # Priors are scaffolding, never data: the first real print replaces
            # the seeded value outright instead of being EWMA-diluted by it —
            # and the bucket re-earns the min_obs gate so a single degenerate
            # print (e.g. a $0.0002 spill dump) can't rule the hour alone.
            self.prior_hours.discard(hour)
            self.by_hour[hour] = clean
            self.counts[hour] = 1
            return
        prev = self.by_hour.get(hour)
        self.by_hour[hour] = clean if prev is None else (1.0 - self.alpha) * prev + self.alpha * clean
        self.counts[hour] = self.counts.get(hour, 0) + 1

    def predict(self, ts: datetime) -> float | None:
        hour = _market_time(ts).hour
        if self.counts.get(hour, 0) < self.min_obs:
            return None
        return self.by_hour[hour]

    def seed_prior(self, values_by_hour: dict[int, float]) -> int:
        """Seed COLD buckets with a shaped prior; returns how many were seeded.

        Seeded buckets predict immediately (counts jump to min_obs) but stay
        flagged in ``prior_hours`` so callers can label them honestly until a
        real observation replaces them."""
        seeded = 0
        for hour, value in values_by_hour.items():
            h = int(hour) % 24
            if self.counts.get(h, 0) > 0:
                continue
            self.by_hour[h] = _clean_float(value)
            self.counts[h] = self.min_obs
            self.prior_hours.add(h)
            seeded += 1
        return seeded

    def is_prior(self, ts: datetime) -> bool:
        return _market_time(ts).hour in self.prior_hours

    def has_real_data(self) -> bool:
        return any(count > 0 and hour not in self.prior_hours for hour, count in self.counts.items())

    def to_state(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "min_obs": self.min_obs,
            "by_hour": {str(k): v for k, v in self.by_hour.items()},
            "counts": {str(k): v for k, v in self.counts.items()},
            "prior_hours": sorted(self.prior_hours),
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> HourlyEwmaProfile:
        profile = cls(alpha=float(state["alpha"]), min_obs=int(state["min_obs"]))
        profile.by_hour = {int(k): float(v) for k, v in state["by_hour"].items()}
        profile.counts = {int(k): int(v) for k, v in state["counts"].items()}
        profile.prior_hours = {int(h) for h in state.get("prior_hours", [])}
        return profile


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
        ramp_ref = self._value_at_or_before(origin_ts - timedelta(minutes=15))
        ramp = 0.0 if ramp_ref is None else last_value - ramp_ref[1]
        return np.asarray(
            [
                1.0,
                *_hour_features(target_ts),
                *_dow_features(target_ts),
                last_value,
                lag_value,
                trend,
                ramp,
            ],
            dtype=float,
        )

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

    @staticmethod
    def _linear_state_matches(state: dict[str, Any]) -> bool:
        return all(
            int(model_state.get("n_features", -1)) == FEATURE_DIM
            for model_state in state["linear"].values()
        )

    def _replay_observations(self, observations: list[tuple[datetime, float]]) -> None:
        """Refit linear models from observations when changing the V1 anchor strategy."""
        for ts, value in observations:
            HorizonModel.observe(self, ts, value)

    def to_state(self) -> dict[str, Any]:
        return {
            "output_bounds": self.output_bounds,
            "max_observations": self.max_observations,
            "linear": {horizon: model.to_state() for horizon, model in self.linear.items()},
            "seasonal": self.seasonal.to_state(),
            "observations": [[_iso(ts), value] for ts, value in self.observations],
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> HorizonModel:
        model = cls(
            output_bounds=tuple(state["output_bounds"]) if state["output_bounds"] is not None else None,
            max_observations=int(state["max_observations"]),
        )
        observations = [(_parse_ts(ts), float(value)) for ts, value in state["observations"]]
        if not cls._linear_state_matches(state):
            raise ValueError("forecast feature schema does not match V1")
        model.linear = {
            horizon: OnlineLinearForecaster.from_state(model_state)
            for horizon, model_state in state["linear"].items()
        }
        model.observations = observations
        model.seasonal = SeasonalPersistence.from_state(state["seasonal"])
        return model


class WeatherForecaster(HorizonModel):
    """Weather forecaster with optional injected NWP base forecasts."""

    def __init__(
        self,
        *,
        nwp_lookup: Callable[[datetime], float | AnchorForecast] | None = None,
        output_bounds: tuple[float, float] | None = None,
        forgetting_factor: float = 0.999,
        max_observations: int = 4096,
        residual_alpha: float = 0.2,
        residual_limit: float | None = None,
        dam_blend_max: float = 0.85,
        dam_blend_full_hours: float = 6.0,
        residual_decay_hours: float = 4.0,
        use_horizon_blend: bool = False,
    ) -> None:
        super().__init__(
            output_bounds=output_bounds,
            forgetting_factor=forgetting_factor,
            max_observations=max_observations,
        )
        self.nwp_lookup = nwp_lookup
        self.residual_alpha = float(residual_alpha)
        self.residual_limit = None if residual_limit is None else max(0.0, float(residual_limit))
        self.dam_blend_max = min(1.0, max(0.0, float(dam_blend_max)))
        self.dam_blend_full_hours = max(1.0e-6, float(dam_blend_full_hours))
        self.residual_decay_hours = max(1.0e-6, float(residual_decay_hours))
        self.use_horizon_blend = bool(use_horizon_blend)
        self.residual_ewma = 0.0
        self.residual_var = 1.0
        self.n_residuals = 0
        self.anchor_source_id: str | None = None

    def _anchor(self, ts: datetime) -> AnchorForecast:
        if self.nwp_lookup is None:
            return AnchorForecast(None, "persistence")
        raw = self.nwp_lookup(ts)
        anchor = raw if isinstance(raw, AnchorForecast) else AnchorForecast(_clean_float(raw), "anchor")
        if anchor.source_id != self.anchor_source_id:
            self.anchor_source_id = anchor.source_id
            self.residual_ewma = 0.0
            self.residual_var = 1.0
            self.n_residuals = 0
        return anchor

    def _anchor_weight(self, delta: timedelta) -> float:
        if not self.use_horizon_blend:
            return 1.0
        if delta <= timedelta(minutes=15):
            return 0.0
        hours = delta.total_seconds() / 3600.0
        return min(self.dam_blend_max, hours / self.dam_blend_full_hours)

    def _residual_weight(self, delta: timedelta) -> float:
        if not self.use_horizon_blend:
            return 1.0
        if delta <= timedelta(minutes=15):
            return 0.0
        hours = delta.total_seconds() / 3600.0
        return exp(-hours / self.residual_decay_hours)

    def _persistence_shape(self, target_ts: datetime) -> float:
        last = self.seasonal.last_value
        return self.seasonal.predict(target_ts) if last is None else last

    def predict(self, sim_ts: datetime) -> dict[str, ForecastPoint]:
        if self.nwp_lookup is None:
            return super().predict(sim_ts)

        result: dict[str, ForecastPoint] = {}
        for horizon in HORIZONS:
            delta = HORIZON_TIMEDELTAS[horizon]
            target_ts = sim_ts + delta
            anchor = self._anchor(target_ts)
            persistence = self._persistence_shape(target_ts)
            if horizon == "5m":
                # Five minutes is deliberately pure persistence: an hourly DAM
                # curve and a slow spread estimate cannot improve this boundary.
                base = persistence if self.use_horizon_blend else persistence + self.residual_ewma
                stderr = 0.0
                provenance = "persistence" if self.use_horizon_blend else "anchor_residual"
            elif anchor.value is None:
                # A missing anchor (DAM coverage gap, cold own-market profile)
                # is an explicit degraded forecast, never a hidden $50 or a
                # stale forward-price substitution.
                weight = self._anchor_weight(delta)
                base = (1.0 - weight) * persistence + weight * self.seasonal.predict(target_ts)
                stderr = sqrt(max(1.0e-9, self.residual_var)) if self.n_residuals else 0.0
                provenance = "p2p_cold_start" if anchor.provenance == "p2p_cold_start" else "degraded_persistence_shape"
            else:
                weight = self._anchor_weight(delta)
                corrected_anchor = anchor.value + self._residual_weight(delta) * self.residual_ewma
                base = (1.0 - weight) * persistence + weight * corrected_anchor
                stderr = sqrt(max(1.0e-9, self.residual_var)) if self.n_residuals else 0.0
                provenance = anchor.provenance
            result[horizon] = ForecastPoint(_clamp(base, self.output_bounds), stderr, provenance)
        return result

    def observe(self, sim_ts: datetime, value: float) -> None:
        clean = _clamp(value, self.output_bounds)
        if self.nwp_lookup is not None:
            anchor = self._anchor(sim_ts)
            if anchor.value is not None:
                residual = clean - anchor.value
                if self.residual_limit is not None:
                    residual = float(np.clip(residual, -self.residual_limit, self.residual_limit))
                if self.n_residuals == 0:
                    self.residual_ewma = residual
                    self.residual_var = 0.0
                else:
                    error = residual - self.residual_ewma
                    self.residual_ewma = (1.0 - self.residual_alpha) * self.residual_ewma + self.residual_alpha * residual
                    if self.residual_limit is not None:
                        self.residual_ewma = float(
                            np.clip(self.residual_ewma, -self.residual_limit, self.residual_limit)
                        )
                    self.residual_var = (1.0 - self.residual_alpha) * self.residual_var + self.residual_alpha * error * error
                self.n_residuals += 1
        super().observe(sim_ts, clean)

    def to_state(self) -> dict[str, Any]:
        state = super().to_state()
        state.update(
            {
                "residual_alpha": self.residual_alpha,
                "residual_limit": self.residual_limit,
                "dam_blend_max": self.dam_blend_max,
                "dam_blend_full_hours": self.dam_blend_full_hours,
                "residual_decay_hours": self.residual_decay_hours,
                "use_horizon_blend": self.use_horizon_blend,
                "residual_ewma": self.residual_ewma,
                "residual_var": self.residual_var,
                "n_residuals": self.n_residuals,
                "anchor_source_id": self.anchor_source_id,
            }
        )
        return state

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any],
        *,
        nwp_lookup: Callable[[datetime], float | AnchorForecast] | None = None,
    ) -> WeatherForecaster:
        model = cls(
            nwp_lookup=nwp_lookup,
            output_bounds=tuple(state["output_bounds"]) if state["output_bounds"] is not None else None,
            max_observations=int(state["max_observations"]),
            residual_alpha=float(state["residual_alpha"]),
            residual_limit=state.get("residual_limit"),
            dam_blend_max=float(state.get("dam_blend_max", 0.85)),
            dam_blend_full_hours=float(state.get("dam_blend_full_hours", 6.0)),
            residual_decay_hours=float(state.get("residual_decay_hours", 4.0)),
            use_horizon_blend=bool(state.get("use_horizon_blend", False)),
        )
        observations = [(_parse_ts(ts), float(value)) for ts, value in state["observations"]]
        if cls._linear_state_matches(state):
            model.linear = {
                horizon: OnlineLinearForecaster.from_state(model_state)
                for horizon, model_state in state["linear"].items()
            }
            model.observations = observations
        else:
            # Replay via the base path (nwp_lookup detached) so the residual
            # tracker is not double-updated; its state is restored below.
            model.nwp_lookup = None
            model._replay_observations(observations)
            model.nwp_lookup = nwp_lookup
        model.seasonal = SeasonalPersistence.from_state(state["seasonal"])
        model.residual_ewma = float(state["residual_ewma"])
        model.residual_var = float(state["residual_var"])
        model.n_residuals = int(state["n_residuals"])
        model.anchor_source_id = state.get("anchor_source_id")
        return model
