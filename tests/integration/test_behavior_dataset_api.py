"""API coverage for trusted market-audit Behavior Dataset export."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from eflux.db.models import MarketAuditEvent, MarketSession, VppStatSnapshot
from eflux.ecosystem import service


async def _login(client, email: str) -> tuple[dict[str, str], int]:
    response = await client.post("/auth/magic-link", json={"email": email})
    assert response.status_code == 200, response.text
    response = await client.post("/auth/consume", json={"token": response.json()["dev_token"]})
    assert response.status_code == 200, response.text
    return (
        {"Authorization": f"Bearer {response.json()['session_token']}"},
        int(response.json()["user_id"]),
    )


@pytest.mark.asyncio
async def test_export_publish_and_download_market_audit_dataset(
    client, db_session, tmp_path, monkeypatch
):
    base = tmp_path / "behavior_datasets"
    monkeypatch.setattr(service, "DATASET_ARTIFACTS_BASE", base)
    auth, user_id = await _login(client, "dataset-api@example.com")
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
            vpp_id=-21,
            name="api-owned",
            owner_id=user_id,
            strategy="scripted",
            tick_no=1,
            sim_ts=now,
            pnl_usd=Decimal("0"),
        )
    )
    db_session.add_all(
        [
            MarketAuditEvent(
                session_id=market_session.id,
                sequence_no=1,
                kind="decision.received",
                interval_id="interval-1",
                participant_id=-21,
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
                participant_id=-21,
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
                participant_id=-21,
                reference_id="interval-1",
                sim_ts=now + timedelta(minutes=5),
                payload={"imbalance_kwh": 0.0, "economic_delta_usd": "0"},
            ),
        ]
    )
    await db_session.commit()

    forged = await client.post(
        "/behavior-datasets",
        headers=auth,
        json={
            "name": "forged",
            "version": "1",
            "market": "realprice",
            "manifest": {"provenance": "platform_verified"},
        },
    )
    assert forged.status_code == 422
    assert "cannot be self-assigned" in forged.text

    forged_attestation = await client.post(
        "/behavior-datasets",
        headers=auth,
        json={
            "name": "forged-attestation",
            "version": "1",
            "market": "realprice",
            "manifest": {"provenance": "externally_attested"},
        },
    )
    assert forged_attestation.status_code == 422
    assert "cannot be self-assigned" in forged_attestation.text

    response = await client.post(
        f"/market-sessions/{market_session.id}/behavior-datasets",
        headers=auth,
        json={
            "name": "api-export",
            "version": "1",
            "participant_ids": [-21],
        },
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert dataset["manifest"]["provenance"] == "platform_verified"
    assert dataset["manifest"]["completeness"]["gateway_rejections"] is True
    assert dataset["row_count"] == 1
    assert dataset["artifact_sha256"]

    replacement = await client.put(
        f"/behavior-datasets/{dataset['id']}/artifact",
        headers=auth,
        params={"artifact_format": "jsonl"},
        content=b'{"forged":true}\n',
    )
    assert replacement.status_code == 409
    assert "cannot be replaced" in replacement.text

    response = await client.post(f"/behavior-datasets/{dataset['id']}/publish", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"

    response = await client.get(f"/behavior-datasets/{dataset['id']}/download", headers=auth)
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\x1f\x8b")
    assert response.headers["x-artifact-sha256"] == dataset["artifact_sha256"]

    response = await client.post(
        f"/market-sessions/{market_session.id}/behavior-datasets",
        headers=auth,
        json={
            "name": "api-unauthorized",
            "version": "1",
            "participant_ids": [-999],
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_upload_self_reported_artifact_then_publish(client, tmp_path, monkeypatch):
    base = tmp_path / "behavior_datasets"
    monkeypatch.setattr(service, "DATASET_ARTIFACTS_BASE", base)
    auth, _ = await _login(client, "dataset-upload@example.com")
    response = await client.post(
        "/behavior-datasets",
        headers=auth,
        json={
            "name": "manual-upload",
            "version": "1",
            "market": "realprice",
            "schema_version": "1",
            "manifest": {"provenance": "self_reported"},
        },
    )
    assert response.status_code == 201, response.text
    dataset = response.json()

    empty = await client.put(
        f"/behavior-datasets/{dataset['id']}/artifact",
        headers=auth,
        params={"artifact_format": "jsonl"},
        content=b"",
    )
    assert empty.status_code == 422

    artifact = (
        b'{"schema_version":"1","decision_id":"d-1","participant_id":-7,'
        b'"sim_ts":"2026-07-01T00:00:00+00:00",'
        b'"observation":{"market":{"interval_id":"i-1"}},'
        b'"action":{"orders":[],"cancels":[],"replaces":[],"is_noop":true},'
        b'"execution_result":{"accepted_order_ids":[],"cancelled_order_ids":[],'
        b'"rejections":[],"fills":[],"unfilled_order_count":0,"fallback":false},'
        b'"outcome":{"economic_delta_usd":"0"},"next_observation":null}\n'
    )
    uploaded = await client.put(
        f"/behavior-datasets/{dataset['id']}/artifact",
        headers=auth,
        params={"artifact_format": "jsonl"},
        content=artifact,
    )
    assert uploaded.status_code == 200, uploaded.text
    payload = uploaded.json()
    assert payload["download_available"] is True
    assert payload["artifact_sha256"]
    assert payload["size_bytes"] == len(artifact)

    secret = "broker-integration-secret"
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            external_attestation_keys={"broker-a": secret}, admin_email_set=set()
        ),
    )
    issued_at = "2026-07-01T00:05:00Z"
    prepared = await client.get(
        f"/behavior-datasets/{dataset['id']}/attestation-payload",
        headers=auth,
        params={"provider_id": "broker-a", "issued_at": issued_at},
    )
    assert prepared.status_code == 200, prepared.text
    canonical = prepared.json()["canonical_payload"]
    invalid = await client.post(
        f"/behavior-datasets/{dataset['id']}/attest",
        headers=auth,
        json={
            "provider_id": "broker-a",
            "issued_at": issued_at,
            "signature_sha256": "0" * 64,
        },
    )
    assert invalid.status_code == 422
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    attested = await client.post(
        f"/behavior-datasets/{dataset['id']}/attest",
        headers=auth,
        json={
            "provider_id": "broker-a",
            "issued_at": issued_at,
            "signature_sha256": signature,
        },
    )
    assert attested.status_code == 200, attested.text
    assert attested.json()["manifest"]["provenance"] == "externally_attested"
    assert attested.json()["manifest"]["external_attestation"]["provider_id"] == "broker-a"
    replacement = await client.put(
        f"/behavior-datasets/{dataset['id']}/artifact",
        headers=auth,
        params={"artifact_format": "jsonl"},
        content=artifact,
    )
    assert replacement.status_code == 409

    published = await client.post(f"/behavior-datasets/{dataset['id']}/publish", headers=auth)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["row_count"] == 1
