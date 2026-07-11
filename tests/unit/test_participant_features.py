"""Participant endowment features retained by the V2 physical model."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from eflux.simulator.agent_spec import validate_vpp_params
from eflux.vpp.der import FlexibleLoad


def test_ev_profile_charges_evening_and_overnight_not_midday():
    load = FlexibleLoad(base_kw=10.0, profile="ev", noise_std=0.0)
    rng = random.Random(0)
    day = datetime(2024, 6, 21, tzinfo=UTC)

    midday = load.draw_kw(day.replace(hour=12), rng)
    evening = load.draw_kw(day.replace(hour=20), rng)
    overnight = load.draw_kw(day.replace(hour=2), rng)

    assert evening > midday * 3
    assert overnight > midday * 3
    assert midday < 2.0


def test_ev_profile_differs_from_residential():
    rng = random.Random(0)
    noon = datetime(2024, 6, 21, 12, tzinfo=UTC)
    ev = FlexibleLoad(base_kw=10.0, profile="ev", noise_std=0.0).draw_kw(noon, rng)
    residential = FlexibleLoad(base_kw=10.0, profile="residential", noise_std=0.0).draw_kw(
        noon, rng
    )
    assert ev < residential


def test_dispatchable_portfolio_cannot_mix_incompatible_behind_meter_resources():
    for bad in (
        {"gas_kw_max": 20.0, "pv_kw_peak": 5.0},
        {"gas_kw_max": 20.0, "wind_kw_rated": 8.0},
        {"gas_kw_max": 20.0, "load_kw_base": 3.0},
        {"gas_kw_max": 20.0, "battery_kwh": 12.0},
        {"gas_kw_max": 20.0, "battery_kw_max": 4.0},
    ):
        with pytest.raises(ValueError, match="gas_kw_max"):
            validate_vpp_params(bad)

    validate_vpp_params(
        {
            "gas_kw_max": 20.0,
            "battery_kwh": 0.0,
            "battery_kw_max": 0.0,
            "pv_kw_peak": 0.0,
            "wind_kw_rated": 0.0,
            "load_kw_base": 0.0,
        }
    )
