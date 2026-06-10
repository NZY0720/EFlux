"""Unit tests for the pvlib-based PV physical model.

Skipped if pvlib not installed. Uses a synthesized weather DataFrame so no network call.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pvlib")


def _synthetic_weather(ts: datetime) -> "pd.DataFrame":
    """One-hour DataFrame with a bright-noon row at the requested timestamp."""
    floored = ts.replace(minute=0, second=0, microsecond=0)
    return pd.DataFrame(
        {
            "ghi": [1000.0],
            "dni": [800.0],
            "dhi": [200.0],
            "temp_air": [25.0],
            "wind_speed": [1.5],
        },
        # ts is already tz-aware (UTC), so pass through directly.
        index=pd.DatetimeIndex([pd.Timestamp(floored)]),
    )


def test_output_kw_returns_significant_power_at_solar_noon():
    from eflux.data.pv_model import PVPhysicalModel

    # Noon UTC in HK (~20.30 local solar). Use equator + flat surface so we get
    # near-peak independent of any seasonal modeling subtlety.
    ts = datetime(2024, 6, 21, 12, 0, tzinfo=UTC)
    model = PVPhysicalModel(lat=0.0, lon=0.0, kw_peak=5.0, tilt=0.0, azimuth=180.0)
    model.weather = _synthetic_weather(ts)
    out = model.output_kw(ts)
    # Equator at noon on flat panel with 1000 W/m² GHI → ~3-5 kW for a 5kW system.
    # We allow a wide band since pvlib's pvwatts inverter applies losses.
    assert 1.5 <= out <= 5.5, f"got {out}"


def test_output_kw_zero_at_night():
    from eflux.data.pv_model import PVPhysicalModel

    ts = datetime(2024, 6, 21, 0, 0, tzinfo=UTC)  # midnight UTC at equator
    model = PVPhysicalModel(lat=0.0, lon=0.0, kw_peak=5.0)
    night = _synthetic_weather(ts).copy()
    night.loc[:, "ghi"] = 0.0
    night.loc[:, "dni"] = 0.0
    night.loc[:, "dhi"] = 0.0
    model.weather = night
    assert model.output_kw(ts) == pytest.approx(0.0, abs=0.01)


def test_output_kw_zero_when_no_weather_attached():
    from eflux.data.pv_model import PVPhysicalModel

    model = PVPhysicalModel(lat=0.0, lon=0.0, kw_peak=5.0)
    assert model.output_kw(datetime(2024, 1, 1, 12, tzinfo=UTC)) == 0.0


def test_output_kw_zero_when_timestamp_not_in_weather():
    from eflux.data.pv_model import PVPhysicalModel

    ts_present = datetime(2024, 6, 21, 12, tzinfo=UTC)
    ts_absent = datetime(2024, 6, 22, 12, tzinfo=UTC)
    model = PVPhysicalModel(lat=0.0, lon=0.0, kw_peak=5.0)
    model.weather = _synthetic_weather(ts_present)
    assert model.output_kw(ts_absent) == 0.0
