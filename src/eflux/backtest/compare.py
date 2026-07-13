"""Honest, descriptive comparison of two completed backtest artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from eflux.evaluation.manifest import build_manifest

COMPARABLE_METRICS = (
    "realized_pnl",
    "mark_to_market",
    "energy_bought_kwh",
    "energy_sold_kwh",
    "trade_count",
    "risk_rejections",
    "unresolved_imbalance_kwh",
    "final_soc_frac",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_participants(path: Path) -> dict[str, dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {str(row["name"]): row for row in csv.DictReader(handle)}


def _number(value: Any) -> float:
    return float(value or 0)


def compare_backtest_runs(left_dir: Path | str, right_dir: Path | str) -> dict[str, Any]:
    left = Path(left_dir)
    right = Path(right_dir)
    left_manifest = _read_json(left / "manifest.json")
    right_manifest = _read_json(right / "manifest.json")
    left_rows = _read_participants(left / "participant_metrics.csv")
    right_rows = _read_participants(right / "participant_metrics.csv")
    names = sorted(set(left_rows) & set(right_rows))
    participant_deltas = []
    for name in names:
        deltas = {
            metric: round(_number(right_rows[name].get(metric)) - _number(left_rows[name].get(metric)), 9)
            for metric in COMPARABLE_METRICS
        }
        participant_deltas.append({"name": name, "delta_right_minus_left": deltas})

    compatibility = {
        field: left_manifest.get(field) == right_manifest.get(field)
        for field in ("market_mode", "start", "end", "tick_seconds", "scenario_sha256")
    }
    parameters = {
        "left_run": left.name,
        "right_run": right.name,
        "left_manifest_sha256": left_manifest.get("evidence_id")
        or left_manifest.get("config_sha256"),
        "right_manifest_sha256": right_manifest.get("evidence_id")
        or right_manifest.get("config_sha256"),
    }
    manifest = build_manifest(
        run_type="comparison",
        market_mode=str(right_manifest.get("market_mode") or "unknown"),
        parameters=parameters,
        scenario_sha256=right_manifest.get("scenario_sha256"),
    )
    return {
        "manifest": {**manifest.model_dump(mode="json"), "evidence_id": manifest.evidence_id},
        "left_run": left.name,
        "right_run": right.name,
        "compatible_fields": compatibility,
        "common_participant_count": len(names),
        "participant_deltas": participant_deltas,
        "methodology": {
            "kind": "descriptive_single_run_delta",
            "confidence_interval": None,
            "note": (
                "These are right-minus-left deltas from one run on each side. "
                "No confidence interval or causal claim is reported without paired replicates."
            ),
        },
    }
