from __future__ import annotations

import csv
import json

import pytest

from eflux.backtest.compare import compare_backtest_runs
from eflux.simulator.scenario_spec import load_scenario_spec


def test_shipped_scenario_has_stable_semantic_hash():
    first = load_scenario_spec("scenarios/p2p.yaml")
    second = load_scenario_spec("scenarios/p2p.yaml")
    assert first.schema_version == "1"
    assert first.market_mode == "p2p"
    assert first.semantic_sha256 == second.semantic_sha256
    assert len(first.semantic_sha256) == 64


def test_scenario_rejects_unknown_top_level_key(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "schema_version: '1'\nname: bad\nmarket_mode: p2p\nparticipants: []\ntypo: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="typo"):
        load_scenario_spec(path)


def test_comparison_is_descriptive_and_has_no_fake_confidence_interval(tmp_path):
    run_dirs = [tmp_path / "left", tmp_path / "right"]
    for index, run_dir in enumerate(run_dirs):
        run_dir.mkdir()
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "market_mode": "p2p",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-02T00:00:00Z",
                    "tick_seconds": 300,
                    "scenario_sha256": "abc",
                    "config_sha256": f"config-{index}",
                }
            ),
            encoding="utf-8",
        )
        with (run_dir / "participant_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["name", "mark_to_market", "trade_count", "risk_rejections"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "name": "agent-a",
                    "mark_to_market": 10 + index * 5,
                    "trade_count": 2 + index,
                    "risk_rejections": index,
                }
            )

    report = compare_backtest_runs(*run_dirs)
    delta = report["participant_deltas"][0]["delta_right_minus_left"]
    assert delta["mark_to_market"] == 5
    assert report["methodology"]["confidence_interval"] is None
    assert report["methodology"]["kind"] == "descriptive_single_run_delta"
