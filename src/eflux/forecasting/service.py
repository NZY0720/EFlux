"""Shared public forecasting service shell."""

from __future__ import annotations

import inspect
import json
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from eflux.config import get_settings
from eflux.forecasting.models import (
    AnchorForecast,
    HorizonModel,
    SeasonalPersistence,
    WeatherForecaster,
)
from eflux.forecasting.schema import (
    HORIZON_TIMEDELTAS,
    HORIZONS,
    ForecastBundle,
    ForecastPoint,
    TargetForecast,
)

MODEL_VERSION = "online-rls-v1"
TARGETS = ("price_real", "price_p2p", "ghi", "temp_air", "wind_speed")
WEATHER_TARGETS = ("ghi", "temp_air", "wind_speed")
HISTORY_MAXLEN = 1500

ForecastOutcome = dict[str, Any]
OutcomeCreatedCallback = Callable[[list[ForecastOutcome]], Awaitable[None] | None]
OutcomeSettledCallback = Callable[[list[ForecastOutcome]], Awaitable[None] | None]


def _price_model(lookup: Callable[[datetime], float | AnchorForecast] | None) -> HorizonModel:
    """Anchored online model when a published forward curve (CAISO DAM) is
    available — the same hybrid used for weather NWP — else plain online RLS."""
    settings = get_settings()
    bounds = (settings.forecast_price_min, settings.forecast_price_max)
    if lookup is None:
        return HorizonModel(output_bounds=bounds)
    return WeatherForecaster(
        nwp_lookup=lookup,
        output_bounds=bounds,
        residual_limit=settings.forecast_dam_residual_limit,
        dam_blend_max=settings.forecast_dam_blend_max,
        dam_blend_full_hours=settings.forecast_dam_blend_full_hours,
        residual_decay_hours=settings.forecast_dam_residual_decay_hours,
        use_horizon_blend=True,
    )


def _upgrade_to_anchored(
    state: dict[str, Any], lookup: Callable[[datetime], float | AnchorForecast]
) -> WeatherForecaster:
    """Rebuild an unanchored V1 price state as an anchored model.

    The persisted RLS weights belong to the un-anchored architecture, so the
    observations are replayed instead; the residual tracker starts fresh and
    re-converges within a handful of observations."""
    settings = get_settings()
    model = WeatherForecaster(
        nwp_lookup=None,
        output_bounds=(settings.forecast_price_min, settings.forecast_price_max),
        max_observations=int(state["max_observations"]),
        residual_limit=settings.forecast_dam_residual_limit,
        dam_blend_max=settings.forecast_dam_blend_max,
        dam_blend_full_hours=settings.forecast_dam_blend_full_hours,
        residual_decay_hours=settings.forecast_dam_residual_decay_hours,
        use_horizon_blend=True,
    )
    model._replay_observations(
        [(datetime.fromisoformat(ts), float(value)) for ts, value in state["observations"]]
    )
    model.seasonal = SeasonalPersistence.from_state(state["seasonal"])
    model.nwp_lookup = lookup
    return model


def _target_from_points(points: dict[str, ForecastPoint]) -> TargetForecast:
    return TargetForecast(h5m=points["5m"], h1h=points["1h"], h12h=points["12h"])


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _bundle_from_state(state: dict[str, Any]) -> ForecastBundle:
    def point(raw: dict[str, Any]) -> ForecastPoint:
        return ForecastPoint(
            float(raw["value"]),
            None if raw["stderr"] is None else float(raw["stderr"]),
            raw.get("provenance"),
        )

    def target(raw: dict[str, Any]) -> TargetForecast:
        return TargetForecast(
            h5m=point(raw["5m"]),
            h1h=point(raw["1h"]),
            h12h=point(raw["12h"]),
        )

    return ForecastBundle(
        as_of=datetime.fromisoformat(state["as_of"]),
        model_version=str(state["model_version"]),
        price_real=target(state["price_real"]),
        price_p2p=target(state["price_p2p"]),
        ghi=target(state["ghi"]),
        temp_air=target(state["temp_air"]),
        wind_speed=target(state["wind_speed"]),
    )


class ForecastService:
    """Owns online models for the five public scalar forecast targets."""

    def __init__(
        self,
        *,
        nwp: dict[str, Callable[[datetime], float | AnchorForecast]] | None = None,
        on_refresh: Callable[[ForecastBundle], None] | None = None,
        on_outcomes_created: OutcomeCreatedCallback | None = None,
        on_outcomes_settled: OutcomeSettledCallback | None = None,
    ) -> None:
        nwp = nwp or {}
        self.models: dict[str, HorizonModel] = {
            "price_real": _price_model(nwp.get("price_real")),
            "price_p2p": _price_model(nwp.get("price_p2p")),
            "ghi": WeatherForecaster(nwp_lookup=nwp.get("ghi"), output_bounds=(0.0, 1500.0)),
            "temp_air": WeatherForecaster(nwp_lookup=nwp.get("temp_air"), output_bounds=(-100.0, 100.0)),
            "wind_speed": WeatherForecaster(nwp_lookup=nwp.get("wind_speed"), output_bounds=(0.0, 100.0)),
        }
        self.on_refresh = on_refresh
        self.on_outcomes_created = on_outcomes_created
        self.on_outcomes_settled = on_outcomes_settled
        self._latest = ForecastBundle.empty()
        self._last_realized: dict[str, float | None] = {name: None for name in TARGETS}
        self._history: deque[dict[str, Any]] = deque(maxlen=HISTORY_MAXLEN)
        self._pending_outcomes: list[ForecastOutcome] = []

    @property
    def latest(self) -> ForecastBundle:
        return self._latest

    def observation_count(self, *targets: str) -> int:
        names = targets or TARGETS
        total = 0
        for name in names:
            if name not in self.models:
                raise KeyError(f"unknown forecast target: {name!r}")
            total += len(self.models[name].observations)
        return total

    @property
    def is_warm(self) -> bool:
        """Price models are the gate because zero-price forecasts are what poison agent behavior."""
        return self.observation_count("price_real", "price_p2p") > 0

    def warm_start(
        self,
        *,
        series: dict[str, Iterable[tuple[datetime, float]]],
        nwp: dict[str, Callable[[datetime], float | AnchorForecast]] | None = None,
    ) -> None:
        if nwp:
            for name, lookup in nwp.items():
                model = self.models.get(name)
                if lookup is not None and isinstance(model, WeatherForecaster):
                    model.nwp_lookup = lookup

        events: list[tuple[datetime, str, float]] = []
        for name, samples in series.items():
            if name not in self.models:
                continue
            events.extend((ts, name, float(value)) for ts, value in samples)
        for ts, name, value in sorted(events, key=lambda item: item[0]):
            self.models[name].observe(ts, value)
            self._last_realized[name] = _finite_or_none(value)

    def observe(self, sim_ts: datetime, **realized: float | None) -> None:
        for name, value in realized.items():
            if name in self.models and value is not None:
                clean = float(value)
                self.models[name].observe(sim_ts, clean)
                self._last_realized[name] = _finite_or_none(clean)
                self._settle_outcomes(name, sim_ts, clean)

    def refresh(self, sim_ts: datetime) -> ForecastBundle:
        forecasts = {name: _target_from_points(self.models[name].predict(sim_ts)) for name in TARGETS}
        self._latest = ForecastBundle(
            as_of=sim_ts,
            model_version=MODEL_VERSION,
            price_real=forecasts["price_real"],
            price_p2p=forecasts["price_p2p"],
            ghi=forecasts["ghi"],
            temp_air=forecasts["temp_air"],
            wind_speed=forecasts["wind_speed"],
        )
        if self.on_refresh is not None:
            self.on_refresh(self._latest)
        self._append_history(self._latest)
        self._record_outcomes(self._latest)
        return self._latest

    def _dispatch(self, callback: OutcomeCreatedCallback | OutcomeSettledCallback | None, rows: list[ForecastOutcome]) -> None:
        if callback is None or not rows:
            return
        result = callback(rows)
        if inspect.isawaitable(result):
            try:
                import asyncio

                asyncio.get_running_loop().create_task(result)
            except RuntimeError:
                # Synchronous/library callers keep the rows in state; the next
                # live refresh will persist new records from an event loop.
                result.close()  # type: ignore[attr-defined]

    def _record_outcomes(self, bundle: ForecastBundle) -> None:
        rows: list[ForecastOutcome] = []
        for market in TARGETS:
            target = getattr(bundle, market)
            model = self.models[market]
            residual = getattr(model, "residual_ewma", None)
            for horizon in HORIZONS:
                point = target.by_horizon(horizon)
                target_ts = bundle.as_of + HORIZON_TIMEDELTAS[horizon]
                anchor_value = None
                if isinstance(model, WeatherForecaster):
                    anchor = model._anchor(target_ts)
                    anchor_value = anchor.value
                row = {
                    "origin_ts": bundle.as_of,
                    "target_ts": target_ts,
                    "horizon": horizon,
                    "market": market,
                    "anchor_value": anchor_value,
                    "residual": residual,
                    "predicted": point.value,
                    "provenance": point.provenance,
                    "realized": None,
                }
                self._pending_outcomes.append(row)
                rows.append(row)
        self._dispatch(self.on_outcomes_created, rows)

    def _settle_outcomes(self, market: str, sim_ts: datetime, realized: float) -> None:
        settled: list[ForecastOutcome] = []
        pending: list[ForecastOutcome] = []
        for row in self._pending_outcomes:
            if row["market"] == market and row["target_ts"] <= sim_ts:
                row = {**row, "realized": realized}
                settled.append(row)
            else:
                pending.append(row)
        self._pending_outcomes = pending
        self._dispatch(self.on_outcomes_settled, settled)

    def _append_history(self, bundle: ForecastBundle) -> None:
        forecasts: dict[str, dict[str, float]] = {}
        for name in TARGETS:
            target = getattr(bundle, name)
            forecasts[name] = {horizon: float(target.by_horizon(horizon).to_dict()["value"]) for horizon in HORIZONS}
        self._history.append(
            {
                "as_of": bundle.as_of.isoformat(),
                "forecasts": forecasts,
                "realized": {name: self._last_realized.get(name) for name in TARGETS},
            }
        )

    def history(self, limit: int | None = None, target: str | None = None) -> list[dict[str, Any]]:
        if target is not None and target not in TARGETS:
            raise KeyError(f"unknown forecast target: {target!r}")
        records = [
            {
                "as_of": record["as_of"],
                "forecasts": {name: dict(values) for name, values in record["forecasts"].items()},
                "realized": dict(record["realized"]),
            }
            for record in self._history
        ]
        if limit is not None:
            records = records[-max(0, int(limit)) :]
        if target is None:
            return records
        return [
            {
                "as_of": record["as_of"],
                "forecasts": {target: record["forecasts"].get(target, {})},
                "realized": {target: record["realized"].get(target)},
            }
            for record in records
        ]

    def save(self, path: str | Path) -> None:
        pending_outcomes = [
            {
                **row,
                "origin_ts": row["origin_ts"].isoformat(),
                "target_ts": row["target_ts"].isoformat(),
            }
            for row in self._pending_outcomes
        ]
        payload = {
            "model_version": MODEL_VERSION,
            "latest": self._latest.to_dict(),
            "last_realized": self._last_realized,
            "history": list(self._history),
            "models": {name: model.to_state() for name, model in self.models.items()},
            "pending_outcomes": pending_outcomes,
        }
        Path(path).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        nwp: dict[str, Callable[[datetime], float | AnchorForecast]] | None = None,
        on_refresh: Callable[[ForecastBundle], None] | None = None,
        on_outcomes_created: OutcomeCreatedCallback | None = None,
        on_outcomes_settled: OutcomeSettledCallback | None = None,
    ) -> ForecastService:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("model_version") != MODEL_VERSION:
            raise ValueError(
                f"incompatible forecast state {payload.get('model_version')!r}; expected {MODEL_VERSION!r}"
            )
        service = cls(
            nwp=nwp,
            on_refresh=on_refresh,
            on_outcomes_created=on_outcomes_created,
            on_outcomes_settled=on_outcomes_settled,
        )
        for name in TARGETS:
            model_state = payload["models"][name]
            lookup = (nwp or {}).get(name)
            if "residual_alpha" in model_state:
                # Anchored model (weather, or a price model saved post-anchor).
                service.models[name] = WeatherForecaster.from_state(model_state, nwp_lookup=lookup)
            elif lookup is not None:
                # Legacy un-anchored price state + an anchor now available.
                service.models[name] = _upgrade_to_anchored(model_state, lookup)
            else:
                service.models[name] = HorizonModel.from_state(model_state)
        last_realized = payload.get("last_realized", {})
        service._last_realized = {
            name: _finite_or_none(last_realized.get(name))
            for name in TARGETS
        }
        if get_settings().forecast_history_reset_on_boot:
            # Session-scoped chart: models restore warm, but the hub starts
            # clean instead of stitching last session's fan/history to this one.
            pass
        else:
            service._latest = _bundle_from_state(payload["latest"])
            service._history = deque(payload.get("history", []), maxlen=HISTORY_MAXLEN)
        service._pending_outcomes = [
            {
                **row,
                "origin_ts": datetime.fromisoformat(row["origin_ts"]),
                "target_ts": datetime.fromisoformat(row["target_ts"]),
            }
            for row in payload.get("pending_outcomes", [])
        ]
        return service

    def to_dict(self) -> dict[str, Any]:
        return self._latest.to_dict()
