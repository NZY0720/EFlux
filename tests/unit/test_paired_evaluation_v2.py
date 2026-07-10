from __future__ import annotations

from decimal import Decimal

import pytest

from eflux.agents.decision import AgentDecision
from eflux.agents.truthful import TruthfulAgent
from eflux.evaluation.paired import evaluate_paired_worlds


def _truthful():
    return TruthfulAgent(price_ref=Decimal("50"))


def test_identical_policies_have_exact_zero_paired_uplift():
    report = evaluate_paired_worlds(
        treatment_name="same-a",
        treatment_factory=_truthful,
        control_name="same-b",
        control_factory=_truthful,
        seeds=[3, 7, 11],
        interval_count=8,
        forecasts_enabled=False,
    )
    assert report.mean_mark_to_market_uplift_usd == pytest.approx(0.0, abs=1e-12)
    assert report.median_mark_to_market_uplift_usd == pytest.approx(0.0, abs=1e-12)
    assert report.mean_imbalance_reduction_kwh == pytest.approx(0.0, abs=1e-12)
    assert report.mean_rejection_reduction == 0.0
    assert report.treatment_win_rate == 0.0
    assert all(pair.realized_pnl_uplift_usd == 0.0 for pair in report.pairs)


def test_paired_world_report_is_repeatable_and_serializable():
    class HoldAgent:
        def decide(self, ctx):
            return AgentDecision.hold("control")

    kwargs = dict(
        treatment_name="truthful",
        treatment_factory=_truthful,
        control_name="hold",
        control_factory=HoldAgent,
        seeds=[5, 9],
        interval_count=6,
        forecasts_enabled=False,
    )
    first = evaluate_paired_worlds(**kwargs)
    second = evaluate_paired_worlds(**kwargs)
    assert first == second
    payload = first.to_dict()
    assert payload["pair_count"] == 2
    assert [pair["seed"] for pair in payload["pairs"]] == [5, 9]


@pytest.mark.parametrize(
    ("seeds", "interval_count", "message"),
    [([], 1, "at least one"), ([1, 1], 1, "unique"), ([1], 0, "positive")],
)
def test_paired_world_input_validation(seeds, interval_count, message):
    with pytest.raises(ValueError, match=message):
        evaluate_paired_worlds(
            treatment_name="a",
            treatment_factory=_truthful,
            control_name="b",
            control_factory=_truthful,
            seeds=seeds,
            interval_count=interval_count,
        )
