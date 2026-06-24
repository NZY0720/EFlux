"""Unit tests for DER component behavior."""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from eflux.vpp.der import PV


class _PhysicalModelWithoutWeather:
    weather = None

    def output_kw(self, sim_ts: datetime) -> float:
        return 0.0


def test_pv_falls_back_to_stub_when_physical_weather_missing():
    pv = PV(kw_peak=5.0, noise_std=0.0, physical_model=_PhysicalModelWithoutWeather())
    noon_site_time = datetime(2026, 5, 27, 12, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))

    assert pv.output_kw(noon_site_time, random.Random(0)) == 5.0


def test_pv_falls_back_to_stub_when_physical_weather_timestamp_absent():
    model = _PhysicalModelWithoutWeather()
    model.weather = pd.DataFrame(index=pd.DatetimeIndex([pd.Timestamp("2026-05-20T04:00:00Z")]))
    pv = PV(kw_peak=5.0, noise_std=0.0, physical_model=model)
    noon_site_time = datetime(2026, 5, 27, 12, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))

    assert pv.output_kw(noon_site_time, random.Random(0)) == 5.0
