"""Shared public forecasting service shell."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from eflux.forecasting.models import HorizonModel, WeatherForecaster
from eflux.forecasting.schema import HORIZONS, ForecastBundle, ForecastPoint, TargetForecast

MODEL_VERSION = "online-rls-v1"
TARGETS = ("price_real", "price_p2p", "ghi", "temp_air", "wind_speed")
WEATHER_TARGETS = ("ghi", "temp_air", "wind_speed")
HISTORY_MAXLEN = 1500


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
        return ForecastPoint(float(raw["value"]), None if raw["stderr"] is None else float(raw["stderr"]))

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
        nwp: dict[str, Callable[[datetime], float]] | None = None,
        on_refresh: Callable[[ForecastBundle], None] | None = None,
    ) -> None:
        nwp = nwp or {}
        self.models: dict[str, HorizonModel] = {
            "price_real": HorizonModel(output_bounds=(-10000.0, 10000.0)),
            "price_p2p": HorizonModel(output_bounds=(-10000.0, 10000.0)),
            "ghi": WeatherForecaster(nwp_lookup=nwp.get("ghi"), output_bounds=(0.0, 1500.0)),
            "temp_air": WeatherForecaster(nwp_lookup=nwp.get("temp_air"), output_bounds=(-100.0, 100.0)),
            "wind_speed": WeatherForecaster(nwp_lookup=nwp.get("wind_speed"), output_bounds=(0.0, 100.0)),
        }
        self.on_refresh = on_refresh
        self._latest = ForecastBundle.empty()
        self._last_realized: dict[str, float | None] = {name: None for name in TARGETS}
        self._history: deque[dict[str, Any]] = deque(maxlen=HISTORY_MAXLEN)

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
        nwp: dict[str, Callable[[datetime], float]] | None = None,
    ) -> None:
        if nwp:
            for name in WEATHER_TARGETS:
                if name in nwp and isinstance(self.models[name], WeatherForecaster):
                    self.models[name].nwp_lookup = nwp[name]

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
        return self._latest

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
        payload = {
            "model_version": MODEL_VERSION,
            "latest": self._latest.to_dict(),
            "last_realized": self._last_realized,
            "history": list(self._history),
            "models": {name: model.to_state() for name, model in self.models.items()},
        }
        Path(path).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        nwp: dict[str, Callable[[datetime], float]] | None = None,
        on_refresh: Callable[[ForecastBundle], None] | None = None,
    ) -> ForecastService:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        service = cls(nwp=nwp, on_refresh=on_refresh)
        for name in ("price_real", "price_p2p"):
            service.models[name] = HorizonModel.from_state(payload["models"][name])
        for name in WEATHER_TARGETS:
            service.models[name] = WeatherForecaster.from_state(
                payload["models"][name],
                nwp_lookup=(nwp or {}).get(name),
            )
        service._latest = _bundle_from_state(payload["latest"])
        last_realized = payload.get("last_realized", {})
        service._last_realized = {
            name: _finite_or_none(last_realized.get(name))
            for name in TARGETS
        }
        service._history = deque(payload.get("history", []), maxlen=HISTORY_MAXLEN)
        return service

    def to_dict(self) -> dict[str, Any]:
        return self._latest.to_dict()
