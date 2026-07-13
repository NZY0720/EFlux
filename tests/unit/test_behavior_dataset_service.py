"""Trust-boundary tests for Behavior Dataset artifacts."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from eflux.db.models import MarketAuditEvent, MarketSession, User, VppStatSnapshot
from eflux.ecosystem import service


def _trajectory_record(*, secret: str | None = None) -> dict:
    observation = {
        "market": {"interval_id": "interval-1"},
        "portfolio": {"soc_kwh": 2.0},
    }
    if secret is not None:
        observation["credentials"] = {"api_key": secret}
    return {
        "schema_version": "1",
        "decision_id": "decision-1",
        "participant_id": -7,
        "sim_ts": "2026-07-01T00:00:00+00:00",
        "observation": observation,
        "action": {
            "rationale": "hold",
            "orders": [],
            "cancels": [],
            "replaces": [],
            "is_noop": True,
        },
        "execution_result": {
            "accepted_order_ids": [],
            "cancelled_order_ids": [],
            "rejections": [],
            "fills": [],
            "unfilled_order_count": 0,
            "slippage_usd": "0",
            "fallback": False,
        },
        "outcome": {"imbalance_kwh": 0.0, "economic_delta_usd": "0"},
        "next_observation": None,
    }


def _write_gzip(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(record))
        handle.write("\n")


@pytest.mark.asyncio
async def test_publish_scans_artifact_and_download_rechecks_digest(
    db_session, tmp_path, monkeypatch
):
    base = tmp_path / "behavior_datasets"
    monkeypatch.setattr(service, "DATASET_ARTIFACTS_BASE", base)
    user = User(email="dataset-publisher@example.com")
    db_session.add(user)
    await db_session.flush()
    path = base / "manual" / "trajectory.jsonl.gz"
    _write_gzip(path, _trajectory_record())

    dataset = await service.create_behavior_dataset(
        db_session,
        user,
        {
            "name": "manual",
            "version": "1",
            "description": "",
            "market": "realprice",
            "visibility": "private",
            "schema_version": "1",
            "manifest": {
                "provenance": "self_reported",
                "completeness": {"observation": False},
            },
            "artifact_path": "manual/trajectory.jsonl.gz",
            "row_count": 999,
            "license": "EFlux-Research-1.0",
            "parent_dataset_id": None,
            "source_release_id": None,
        },
    )
    published = await service.publish_behavior_dataset(db_session, dataset.id, user)

    assert published.row_count == 1
    assert all(published.manifest["completeness"].values())
    assert published.manifest["observed"]["no_op_count"] == 1
    assert published.manifest["redaction"]["status"] == "verified"
    assert published.artifact_sha256
    assert (await service.get_dataset_download(db_session, dataset.id, user))[1] == path

    with path.open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(service.EcosystemError, match="published digest"):
        await service.get_dataset_download(db_session, dataset.id, user)


@pytest.mark.asyncio
async def test_publish_rejects_secret_and_user_cannot_claim_platform_provenance(
    db_session, tmp_path, monkeypatch
):
    base = tmp_path / "behavior_datasets"
    monkeypatch.setattr(service, "DATASET_ARTIFACTS_BASE", base)
    user = User(email="dataset-secrets@example.com")
    db_session.add(user)
    await db_session.flush()

    with pytest.raises(service.EcosystemError, match="escapes the artifact directory"):
        service._resolve_dataset_artifact_value("../outside.jsonl.gz", must_exist=False)

    with pytest.raises(service.EcosystemError, match="cannot be self-assigned"):
        await service.create_behavior_dataset(
            db_session,
            user,
            {
                "name": "forged",
                "version": "1",
                "market": "realprice",
                "manifest": {"provenance": "platform_verified"},
            },
        )

    path = base / "secret" / "trajectory.jsonl.gz"
    _write_gzip(path, _trajectory_record(secret="live-secret"))
    dataset = await service.create_behavior_dataset(
        db_session,
        user,
        {
            "name": "secret",
            "version": "1",
            "market": "realprice",
            "manifest": {},
            "artifact_path": "secret/trajectory.jsonl.gz",
        },
    )
    with pytest.raises(service.EcosystemError, match="unredacted secret material"):
        await service.publish_behavior_dataset(db_session, dataset.id, user)


@pytest.mark.asyncio
async def test_platform_market_session_export_is_owned_complete_and_publishable(
    db_session, tmp_path, monkeypatch
):
    base = tmp_path / "behavior_datasets"
    monkeypatch.setattr(service, "DATASET_ARTIFACTS_BASE", base)
    user = User(email="dataset-export@example.com")
    db_session.add(user)
    await db_session.flush()
    now = datetime(2026, 7, 1, tzinfo=UTC)
    market_session = MarketSession(
        market_mode="realprice",
        started_at=now,
        ended_at=now + timedelta(hours=1),
        price_ref=Decimal("50"),
    )
    db_session.add(market_session)
    await db_session.flush()
    db_session.add(
        VppStatSnapshot(
            session_id=market_session.id,
            vpp_id=-7,
            name="owned",
            owner_id=user.id,
            strategy="scripted",
            tick_no=1,
            sim_ts=now,
            pnl_usd=Decimal("0"),
        )
    )
    events = [
        MarketAuditEvent(
            session_id=market_session.id,
            sequence_no=1,
            kind="decision.received",
            interval_id="interval-1",
            participant_id=-7,
            reference_id="decision-1",
            sim_ts=now,
            payload={
                "observation": {
                    "market": {"interval_id": "interval-1"},
                    "portfolio": {"soc_kwh": 2.0},
                },
                "orders": [],
                "cancels": [],
                "replaces": [],
                "rationale": "hold",
            },
        ),
        MarketAuditEvent(
            session_id=market_session.id,
            sequence_no=2,
            kind="gateway.accepted",
            interval_id="interval-1",
            participant_id=-7,
            reference_id="decision-1",
            sim_ts=now,
            payload={
                "execution_result": {
                    "accepted_order_ids": [],
                    "cancelled_order_ids": [],
                    "rejections": [],
                    "fills": [],
                    "unfilled_order_count": 0,
                    "slippage_usd": "0",
                    "fallback": False,
                }
            },
        ),
        MarketAuditEvent(
            session_id=market_session.id,
            sequence_no=3,
            kind="delivery.settled",
            interval_id="interval-1",
            participant_id=-7,
            reference_id="interval-1",
            sim_ts=now + timedelta(minutes=5),
            payload={"imbalance_kwh": 0.0, "economic_delta_usd": "0"},
        ),
    ]
    db_session.add_all(events)
    await db_session.flush()

    dataset = await service.export_market_session_dataset(
        db_session,
        market_session.id,
        user,
        {
            "name": "platform-export",
            "version": "1",
            "description": "",
            "visibility": "private",
            "participant_ids": [-7],
            "source_release_id": None,
            "license": "EFlux-Research-1.0",
        },
    )

    assert dataset.manifest["provenance"] == "platform_verified"
    assert dataset.manifest["generated_by"]["market_session_id"] == market_session.id
    assert dataset.row_count == 1
    assert dataset.artifact_path.endswith(".jsonl.gz")
    with gzip.open(base / dataset.artifact_path, "rt", encoding="utf-8") as handle:
        assert json.loads(handle.readline())["action"]["is_noop"] is True
    published = await service.publish_behavior_dataset(db_session, dataset.id, user)
    assert published.status == "published"

    with pytest.raises(service.EcosystemError, match="not owned"):
        await service.export_market_session_dataset(
            db_session,
            market_session.id,
            user,
            {
                "name": "unauthorized",
                "version": "1",
                "participant_ids": [-99],
            },
        )
