"""Unit tests for score v1 (eflux.stats.score) — the endowment-normalized leaderboard metric."""

from __future__ import annotations

import pytest

from eflux.stats.score import (
    MIN_ELAPSED_H,
    MIN_POWER_SCALE_KW,
    compute_score,
    power_scale_kw,
    revenue_scale_usd,
)


def test_power_scale_sums_all_nameplate_fields():
    params = {
        "pv_kw_peak": 4.0,
        "wind_kw_rated": 3.0,
        "battery_kw_max": 5.0,
        "gas_kw_max": 10.0,
        "load_kw_base": 2.0,
        "battery_kwh": 999.0,  # energy, not power — must be ignored
    }
    assert power_scale_kw(params) == pytest.approx(24.0)


def test_power_scale_floors_empty_endowment():
    assert power_scale_kw({}) == MIN_POWER_SCALE_KW
    assert power_scale_kw({"pv_kw_peak": 0.0}) == MIN_POWER_SCALE_KW


def test_score_definition():
    # 10 kW endowment flat-out at 50 $/MWh for 10 h earns 10*10*50/1000 = $5.
    # A $5 PnL over that window scores exactly 1.0.
    params = {"pv_kw_peak": 10.0}
    assert compute_score(5.0, params, price_ref=50.0, elapsed_h=10.0) == pytest.approx(1.0)
    assert compute_score(-5.0, params, price_ref=50.0, elapsed_h=10.0) == pytest.approx(-1.0)


def test_score_is_endowment_normalized():
    # Same PnL, 10x the endowment -> a tenth of the score: big batteries don't auto-win.
    small = compute_score(5.0, {"battery_kw_max": 5.0}, 50.0, 10.0)
    big = compute_score(5.0, {"battery_kw_max": 50.0}, 50.0, 10.0)
    assert small == pytest.approx(10.0 * big)


def test_elapsed_floor_protects_fresh_agents():
    # Seconds-old agents are scored as if observed for MIN_ELAPSED_H, not near-zero.
    burst = compute_score(1.0, {"pv_kw_peak": 1.0}, 50.0, elapsed_h=0.001)
    floored = compute_score(1.0, {"pv_kw_peak": 1.0}, 50.0, elapsed_h=MIN_ELAPSED_H)
    assert burst == pytest.approx(floored)


def test_revenue_scale_matches_score_denominator():
    params = {"gas_kw_max": 8.0}
    scale = revenue_scale_usd(params, 60.0, 4.0)
    assert compute_score(scale, params, 60.0, 4.0) == pytest.approx(1.0)


def test_none_and_missing_fields_are_zero():
    assert power_scale_kw({"pv_kw_peak": None}) == MIN_POWER_SCALE_KW
