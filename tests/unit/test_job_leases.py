from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from eflux import job_leases
from eflux.db.models import AgentRelease, ReleaseEvaluation, User
from eflux.db.session import get_sessionmaker
from eflux.ecosystem.worker import claim_next_ecosystem_job
from eflux.job_leases import JobLeaseLost, maintain_job_lease


async def _release_evaluation(db_session) -> ReleaseEvaluation:
    owner = User(email=f"lease-{datetime.now(UTC).timestamp()}@example.com")
    db_session.add(owner)
    await db_session.flush()
    release = AgentRelease(
        owner_id=owner.id,
        name="leased",
        version="1",
        market="realprice",
        visibility="private",
        status="published",
        recipe={"algorithm": "scripted"},
        state={},
        compatibility={},
        environment={},
        badges=[],
        content_sha256="b" * 64,
    )
    db_session.add(release)
    await db_session.flush()
    evaluation = ReleaseEvaluation(
        release_id=release.id,
        requested_by_id=owner.id,
        kind="deterministic_replay",
        status="queued",
        provenance="platform_verified",
        config={},
        metrics={},
    )
    db_session.add(evaluation)
    await db_session.commit()
    return evaluation


async def test_expired_ecosystem_lease_is_reclaimed(db_session) -> None:
    evaluation = await _release_evaluation(db_session)
    expired_claim = datetime.now(UTC) - timedelta(minutes=5)
    evaluation.status = "running"
    evaluation.claimed_at = expired_claim
    evaluation.lease_expires_at = expired_claim + timedelta(minutes=1)
    await db_session.commit()

    job = await claim_next_ecosystem_job(get_sessionmaker())

    assert job is not None
    assert (job.kind, job.id) == ("evaluation", evaluation.id)
    await db_session.refresh(evaluation)
    assert evaluation.status == "running"
    assert evaluation.claimed_at != expired_claim


async def test_live_ecosystem_lease_is_not_reclaimed(db_session) -> None:
    evaluation = await _release_evaluation(db_session)
    claimed_at = datetime.now(UTC)
    evaluation.status = "running"
    evaluation.claimed_at = claimed_at
    evaluation.lease_expires_at = claimed_at + timedelta(minutes=5)
    await db_session.commit()

    assert await claim_next_ecosystem_job(get_sessionmaker()) is None
    await db_session.refresh(evaluation)
    assert evaluation.status == "running"
    assert evaluation.claimed_at is not None
    assert evaluation.claimed_at.replace(tzinfo=UTC) == claimed_at


async def test_worker_is_interrupted_when_its_lease_is_taken_over(
    db_session, monkeypatch
) -> None:
    evaluation = await _release_evaluation(db_session)
    factory = get_sessionmaker()
    job = await claim_next_ecosystem_job(factory)
    assert job is not None
    monkeypatch.setattr(job_leases, "JOB_HEARTBEAT_SECONDS", 0.01)
    entered = asyncio.Event()
    never = asyncio.Event()

    async def old_worker() -> None:
        async with maintain_job_lease(factory, ReleaseEvaluation, evaluation.id):
            entered.set()
            await never.wait()

    task = asyncio.create_task(old_worker())
    await asyncio.wait_for(entered.wait(), timeout=1)
    takeover = datetime.now(UTC) + timedelta(seconds=1)
    evaluation.status = "running"
    evaluation.claimed_at = takeover
    evaluation.lease_expires_at = takeover + timedelta(minutes=2)
    await db_session.commit()

    with pytest.raises(JobLeaseLost, match="Lost lease"):
        await asyncio.wait_for(task, timeout=1)

    await db_session.refresh(evaluation)
    assert evaluation.status == "running"
    assert evaluation.claimed_at is not None
    assert evaluation.claimed_at.replace(tzinfo=UTC) == takeover
