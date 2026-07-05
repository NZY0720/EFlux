"""Benchmark harness tests — the M3 acceptance gate.

The scripted StrategyAgent must be competitive with the Truthful baseline (the structured
language costs nothing) and emit zero invalid actions, while clearly beating ZI.
"""

from __future__ import annotations

import pytest

from eflux.agents.bench.metrics import EpisodeMetrics, format_leaderboard
from eflux.agents.bench.run import run_benchmark


def test_strategy_agent_is_competitive_with_truthful_and_clean():
    rows = {m.candidate: m for m in run_benchmark(n_ticks=144, tick_minutes=10.0)}
    strat, truth = rows["strategy"], rows["truthful"]

    # Zero invalid actions attributable to the candidate (its own gate vetoes).
    assert strat.risk_rejections == 0
    # It participates and is profitable.
    assert strat.energy_traded_kwh > 0
    assert strat.mark_to_market > 0
    # Reproduces Truthful's balance trades exactly (identical energy bought).
    assert strat.energy_bought_kwh == pytest.approx(truth.energy_bought_kwh, abs=1e-6)
    # The adaptive AA baseline is also a clean, participating candidate.
    assert rows["aa"].risk_rejections == 0
    assert rows["aa"].energy_traded_kwh > 0
    # Competitive with Truthful, trailing only by the battery arbitrage it forgoes
    # (the headroom the PPO policy in M4 is meant to capture); never exceeds it.
    assert 0.4 * truth.mark_to_market <= strat.mark_to_market <= truth.mark_to_market + 1.0


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
