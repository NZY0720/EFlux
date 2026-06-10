"""Unit tests for the Open-Meteo client's endpoint selection."""

from __future__ import annotations

from datetime import date, timedelta

from eflux.data.weather import ARCHIVE_URL, FORECAST_URL, _endpoint_for


def test_fully_historical_range_uses_archive():
    assert _endpoint_for(date.today() - timedelta(days=7)) == ARCHIVE_URL


def test_range_touching_today_uses_forecast():
    # Regression for the never-working "real PV physics" path: the simulator
    # runs at wall-clock time, so its weather window ends in the future — the
    # archive endpoint (which lags real-time) could never cover it.
    assert _endpoint_for(date.today()) == FORECAST_URL
    assert _endpoint_for(date.today() + timedelta(days=2)) == FORECAST_URL
