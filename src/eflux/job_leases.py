"""Small database leases shared by the durable background-job workers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

JOB_LEASE_DURATION = timedelta(minutes=2)
JOB_HEARTBEAT_SECONDS = 30.0


class JobLeaseLost(RuntimeError):
    """Raised in a worker whose claim was requeued or taken over."""


async def requeue_expired_jobs(
    session: AsyncSession, *models: type[Any], now: datetime | None = None
) -> None:
    """Return abandoned running rows to the queue before the next claim."""

    current = now or datetime.now(UTC)
    for model in models:
        await session.execute(
            update(model)
            .where(
                model.status == "running",
                or_(model.lease_expires_at.is_(None), model.lease_expires_at <= current),
            )
            .values(status="queued", claimed_at=None, lease_expires_at=None)
        )


def lease_values(now: datetime | None = None) -> dict[str, datetime]:
    claimed_at = now or datetime.now(UTC)
    return {
        "claimed_at": claimed_at,
        "lease_expires_at": claimed_at + JOB_LEASE_DURATION,
    }


@asynccontextmanager
async def maintain_job_lease(
    session_factory: async_sessionmaker[AsyncSession], model: type[Any], job_id: int
) -> AsyncIterator[None]:
    """Refresh one claim while its worker coroutine is executing."""

    async with session_factory() as session:
        claimed_at = (
            await session.execute(
                select(model.claimed_at).where(model.id == job_id, model.status == "running")
            )
        ).scalar_one_or_none()
    if claimed_at is None:
        raise RuntimeError(f"{model.__name__} {job_id} has no active lease")

    owner_task = asyncio.current_task()
    if owner_task is None:
        raise RuntimeError("job leases require an asyncio task")
    stop = asyncio.Event()
    lost = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=JOB_HEARTBEAT_SECONDS)
                return
            except TimeoutError:
                pass
            try:
                async with session_factory() as session:
                    refreshed = await session.execute(
                        update(model)
                        .where(
                            model.id == job_id,
                            model.status == "running",
                            model.claimed_at == claimed_at,
                        )
                        .values(lease_expires_at=datetime.now(UTC) + JOB_LEASE_DURATION)
                    )
                    await session.commit()
                if refreshed.rowcount != 1:
                    async with session_factory() as session:
                        state = (
                            await session.execute(
                                select(model.status, model.claimed_at).where(model.id == job_id)
                            )
                        ).one_or_none()
                    # A terminal transition is the normal race at the end of a job.
                    # Queued/running or a missing row means this worker no longer owns it.
                    if state is not None and state[0] not in {"queued", "running"}:
                        return
                    log.error("Lost lease for %s id=%s", model.__name__, job_id)
                    lost.set()
                    owner_task.cancel()
                    return
            except Exception:
                log.exception("Failed to refresh lease for %s id=%s", model.__name__, job_id)

    task = asyncio.create_task(heartbeat())
    try:
        yield
    except asyncio.CancelledError as exc:
        if lost.is_set():
            raise JobLeaseLost(f"Lost lease for {model.__name__} id={job_id}") from exc
        raise
    finally:
        stop.set()
        try:
            await task
        except asyncio.CancelledError as exc:
            if lost.is_set():
                raise JobLeaseLost(f"Lost lease for {model.__name__} id={job_id}") from exc
            raise
