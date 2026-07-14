from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eflux.market.metering import IntervalMeterBook
from eflux.market.products import DeliveryInterval


def _interval() -> DeliveryInterval:
    start = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
    return DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )


def test_one_second_power_samples_integrate_to_interval_kwh():
    meters = IntervalMeterBook()
    interval = _interval()
    for _ in range(300):
        meter = meters.integrate(
            participant_id=1,
            interval=interval,
            renewable_power_kw=6.0,
            uncontrolled_load_power_kw=3.0,
            duration_sec=1.0,
        )
    assert meter.renewable_generation_kwh == pytest.approx(0.5)
    assert meter.uncontrolled_load_kwh == pytest.approx(0.25)
    assert meter.integrated_duration_sec == pytest.approx(300.0)


def test_meter_rejects_energy_beyond_interval_duration():
    meters = IntervalMeterBook()
    interval = _interval()
    meters.integrate(
        participant_id=1,
        interval=interval,
        renewable_power_kw=1.0,
        uncontrolled_load_power_kw=0.0,
        duration_sec=300.0,
    )
    with pytest.raises(ValueError, match="exceeds delivery interval"):
        meters.integrate(
            participant_id=1,
            interval=interval,
            renewable_power_kw=1.0,
            uncontrolled_load_power_kw=0.0,
            duration_sec=1.0,
        )
