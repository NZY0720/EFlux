"""Focused contract tests for the agent ecosystem foundations."""

from __future__ import annotations

import gzip
import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eflux.backtest.runner import _participant_metrics
from eflux.datasets.trajectory import (
    DATASET_SCHEMA_VERSION,
    build_trajectory_rows,
    export_trajectory_jsonl_gz,
    redact_secrets,
)
from eflux.db.models import (
    AgentRelease,
    BehaviorDataset,
    DatasetTrainingRun,
    PopulationPack,
    ReleaseEvaluation,
)
from eflux.vpp.base import VPPParams


def test_ecosystem_models_expose_versioned_content_and_domain_constraints():
    release = AgentRelease(
        owner_id=7,
        name="dispatch-agent",
        version="1.2.0",
        market="hybrid",
        recipe={"algorithm": "battery_arbitrageur", "llm": {"model": "example-model"}},
        state={"memory_artifact_sha256": "a" * 64},
        compatibility={"dataset_schema": DATASET_SCHEMA_VERSION},
        environment={"runtime": "eflux"},
        badges=["Reproducible"],
        parent_release_id=3,
        content_sha256="b" * 64,
    )
    assert release.recipe["algorithm"] == "battery_arbitrageur"
    assert release.state["memory_artifact_sha256"] == "a" * 64
    assert release.compatibility == {"dataset_schema": DATASET_SCHEMA_VERSION}
    assert release.environment == {"runtime": "eflux"}
    assert release.badges == ["Reproducible"]
    assert release.parent_release_id == 3

    dataset = BehaviorDataset(
        owner_id=7,
        name="operator-holds",
        version="2026.07",
        market="realprice",
        schema_version=DATASET_SCHEMA_VERSION,
        manifest={"redaction": {"secrets": True}, "provenance": "platform_verified"},
        artifact_path="behavior_datasets/operator-holds.jsonl.gz",
        artifact_sha256="c" * 64,
        size_bytes=123,
        row_count=2,
        license="EFlux-Research-1.0",
        source_release_id=11,
        content_sha256="d" * 64,
    )
    assert dataset.manifest["redaction"]["secrets"] is True
    assert dataset.artifact_sha256 == "c" * 64
    assert dataset.row_count == 2
    assert dataset.source_release_id == 11

    expected_contracts = {
        AgentRelease: {
            "uq_agent_release_version",
            "ck_agent_releases_market",
            "ck_agent_releases_visibility",
            "ck_agent_releases_status",
        },
        ReleaseEvaluation: {
            "ck_release_evaluations_kind",
            "ck_release_evaluations_status",
            "ck_release_evaluations_provenance",
        },
        BehaviorDataset: {
            "uq_behavior_dataset_version",
            "ck_behavior_datasets_market",
            "ck_behavior_datasets_visibility",
            "ck_behavior_datasets_status",
        },
        DatasetTrainingRun: {
            "ck_dataset_training_algorithm",
            "ck_dataset_training_status",
        },
        PopulationPack: {
            "uq_population_pack_version",
            "ck_population_packs_visibility",
            "ck_population_packs_status",
        },
    }
    for model, expected_names in expected_contracts.items():
        constraint_names = {
            constraint.name
            for constraint in model.__table__.constraints
            if constraint.name is not None
        }
        assert expected_names <= constraint_names


def test_trajectory_build_and_export_preserve_negative_examples_and_redact_secrets(tmp_path):
    first_observation = {
        "market": {"interval_id": "interval-1"},
        "credentials": {
            "api_key": "must-not-leak",
            "nested": [{"Authorization": "Bearer must-not-leak"}],
        },
    }
    second_observation = {
        "market": {"interval_id": "interval-2"},
        "portfolio": {"soc_kwh": 4.5},
    }
    events = [
        {
            "sequence_no": 4,
            "kind": "gateway.accepted",
            "interval_id": "interval-2",
            "participant_id": 9,
            "reference_id": "decision-2",
            "sim_ts": "2026-01-01T00:05:00+00:00",
            "payload": {
                "execution_result": {
                    "rejections": [],
                    "fills": [],
                    "unfilled_order_count": 1,
                }
            },
        },
        {
            "sequence_no": 1,
            "kind": "decision.received",
            "interval_id": "interval-1",
            "participant_id": 9,
            "reference_id": "decision-1",
            "sim_ts": "2026-01-01T00:00:00+00:00",
            "payload": {
                "observation": first_observation,
                "rationale": "hold",
                "orders": [],
                "cancels": [],
                "replaces": [],
            },
        },
        {
            "sequence_no": 3,
            "kind": "decision.received",
            "interval_id": "interval-2",
            "participant_id": 9,
            "reference_id": "decision-2",
            "sim_ts": "2026-01-01T00:05:00+00:00",
            "payload": {
                "observation": second_observation,
                "orders": [{"side": "buy", "qty_kwh": "1"}],
                "cancels": [],
                "replaces": [],
            },
        },
        {
            "sequence_no": 2,
            "kind": "gateway.rejected",
            "interval_id": "interval-1",
            "participant_id": 9,
            "reference_id": "decision-1",
            "sim_ts": "2026-01-01T00:00:00+00:00",
            "payload": {"rejections": [{"reason": "gate_closed", "request": {"side": "sell"}}]},
        },
    ]

    rows = build_trajectory_rows(events)

    assert [row["decision_id"] for row in rows] == ["decision-1", "decision-2"]
    assert rows[0]["action"]["is_noop"] is True
    assert rows[0]["execution_result"]["rejections"][0]["reason"] == "gate_closed"
    assert rows[0]["next_observation"] == second_observation
    assert rows[1]["execution_result"]["unfilled_order_count"] == 1
    assert rows[1]["next_observation"] is None

    target = tmp_path / "trajectory.jsonl.gz"
    metadata = export_trajectory_jsonl_gz(rows, target)
    assert metadata["row_count"] == 2
    assert metadata["size_bytes"] == target.stat().st_size
    assert metadata["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()

    with gzip.open(target, "rt", encoding="utf-8") as handle:
        exported = [json.loads(line) for line in handle]
    credentials = exported[0]["observation"]["credentials"]
    assert credentials["api_key"] == "[REDACTED]"
    assert credentials["nested"][0]["Authorization"] == "[REDACTED]"
    assert "must-not-leak" not in target.read_bytes().decode("latin-1")
    assert redact_secrets({"outer": ({"password": "hidden"},)}) == {
        "outer": [{"password": "[REDACTED]"}]
    }


def test_mark_to_market_converts_kwh_at_usd_per_mwh_to_usd():
    participant = SimpleNamespace(
        vpp_id=1,
        name="one-kwh-exposure",
        strategy="test",
        is_my_vpp=False,
        mirror_of=None,
        params=VPPParams(),
        state=SimpleNamespace(
            pnl=Decimal("0"),
            pending_net_kwh=1.0,
            cumulative_energy_bought_kwh=0.0,
            cumulative_energy_sold_kwh=0.0,
        ),
        battery=SimpleNamespace(soc_kwh=0.0, soc_frac=0.0),
        trade_count=0,
    )
    simulator = SimpleNamespace(
        engine=SimpleNamespace(last_price=Decimal("50")),
        vpps={participant.vpp_id: participant},
        risk_rejections_by_vpp={},
        _open_orders_net_by_vpp=lambda: {},
    )

    rows = _participant_metrics(simulator)

    assert rows[0]["mark_to_market"] == pytest.approx(0.05)
