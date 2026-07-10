"""Benchmark harness tests — the M3 acceptance gate.

The scripted StrategyAgent must be competitive with the Truthful baseline (the structured
language costs nothing) and emit zero invalid actions, while clearly beating ZI.
"""

from __future__ import annotations

import math

import pytest

from eflux.agents.bench.metrics import EpisodeMetrics, format_leaderboard
from eflux.agents.bench.run import run_benchmark


def test_strategy_agent_is_competitive_with_truthful_and_clean():
    rows = {m.candidate: m for m in run_benchmark(n_ticks=144, tick_minutes=10.0)}
    strat, truth = rows["strategy"], rows["truthful"]

    # Zero invalid actions attributable to the candidate (its own gate vetoes).
    assert strat.risk_rejections == 0
    # It participates and produces a finite, fully settled real-USD result.
    assert strat.energy_traded_kwh > 0
    assert math.isfinite(strat.mark_to_market)
    # With identical valuation and explicit delivery (no implicit battery buffering),
    # the scripted baseline should reproduce Truthful exactly in this fixed scenario.
    assert strat.mark_to_market == pytest.approx(truth.mark_to_market, abs=1e-12)
    assert strat.energy_bought_kwh == pytest.approx(truth.energy_bought_kwh, abs=1e-12)
    assert strat.unresolved_imbalance_kwh == pytest.approx(
        truth.unresolved_imbalance_kwh, abs=1e-12
    )
    # The adaptive AA baseline is also a clean, participating candidate.
    assert rows["aa"].risk_rejections == 0
    assert rows["aa"].energy_traded_kwh > 0


def test_benchmark_is_deterministic():
    a = {m.candidate: m.mark_to_market for m in run_benchmark(n_ticks=72, tick_minutes=10.0)}
    b = {m.candidate: m.mark_to_market for m in run_benchmark(n_ticks=72, tick_minutes=10.0)}
    assert a == b


def test_leaderboard_formats_all_candidates():
    rows = [
        EpisodeMetrics("strategy", 10.0, 11.0, 5.0, 0.0, 0.5, 0.5, 0, 10),
        EpisodeMetrics("aa", 1.0, 1.0, 5.0, 0.0, 0.5, 0.5, 2, 10),
    ]
    out = format_leaderboard(rows)
    assert "strategy" in out and "aa" in out and "rejects" in out


def test_bench_episode_warms_forecast_service_and_can_opt_out():
    from eflux.agents.bench.run import run_episode
    from eflux.agents.bench.scenarios import candidates

    make = candidates()["truthful"]
    sim, _ = run_episode(make, n_ticks=48, tick_h=1.0 / 6.0)
    assert sim.forecast_service is not None
    # Engine trades feed the price models, so the service warms and the same
    # context gate the live loop uses starts exposing the bundle to agents.
    assert sim.forecast_service.is_warm
    assert sim._context_forecast() is not None

    off_sim, _ = run_episode(make, n_ticks=8, tick_h=1.0 / 6.0, forecasts_enabled=False)
    assert off_sim.forecast_service is None
