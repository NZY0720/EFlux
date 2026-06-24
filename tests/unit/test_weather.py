"""Unit tests for the Open-Meteo client's endpoint selection."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from eflux.data.weather import ARCHIVE_URL, FORECAST_URL, _endpoint_for


def test_fully_historical_range_uses_archive():
    assert _endpoint_for(date.today() - timedelta(days=7)) == ARCHIVE_URL


def test_range_touching_today_uses_forecast():
    # Regression for the never-working "real PV physics" path: the simulator
    # runs at wall-clock time, so its weather window ends in the future — the
    # archive endpoint (which lags real-time) could never cover it.
    assert _endpoint_for(date.today()) == FORECAST_URL
    assert _endpoint_for(date.today() + timedelta(days=2)) == FORECAST_URL


def test_fetch_hourly_sync_requests_wind_speed_ms(monkeypatch, tmp_path):
    pytest.importorskip("pandas")

    from eflux.data import weather

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "hourly": {
                    "time": ["2026-06-24T00:00"],
                    "shortwave_radiation": [0.0],
                    "direct_normal_irradiance": [0.0],
                    "diffuse_radiation": [0.0],
                    "temperature_2m": [20.0],
                    "wind_speed_10m": [4.2],
                }
            }

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, endpoint, params):
            captured["endpoint"] = endpoint
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(weather.httpx, "Client", FakeClient)

    weather.fetch_hourly_sync(
        34.05,
        -118.25,
        date(2026, 6, 24),
        date(2026, 6, 24),
        cache_dir=tmp_path,
    )

    assert captured["params"]["wind_speed_unit"] == "ms"
