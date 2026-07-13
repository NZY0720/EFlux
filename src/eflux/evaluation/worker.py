"""Official evaluation queue worker.

Run continuously with ``python -m eflux.evaluation.worker`` or claim at most one run
with ``--once`` (used by CI and operational drain jobs).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eflux.config import get_settings
from eflux.db.models import (
    AuditEvent,
    Competition,
    CompetitionRuleSet,
    EvaluationMetric,
    EvaluationRun,
    EvaluationSeedRun,
    ProveOutRun,
    Submission,
)
from eflux.db.session import get_sessionmaker
from eflux.evaluation.manifest import content_sha256
from eflux.evaluation.proveout import run_proveout_execution
from eflux.evaluation.scoring import SeedScore, score_seed
from eflux.evaluation.seeds import DEFAULT_ROUND, derive_seed, seed_labels

log = logging.getLogger(__name__)
MAX_SEED_ATTEMPTS = 3


@dataclass(frozen=True)
class RunContext:
    run_id: int
    submission_id: int
    actor_user_id: int
    competition_slug: str
    kind: str
    rules_version: str
    payload: dict[str, Any]
    rules_config: dict[str, Any]
    manifest: dict[str, Any] | None


async def claim_next_run(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int | None:
    """Optimistically claim the oldest queued run without relying on row locks.

    The guarded UPDATE is the ownership decision on both SQLite and Postgres. If a
    competing worker wins after our SELECT, start a fresh transaction and try the next
    visible queued row; eventually this returns a distinct run or a no-op.
    """
    factory = session_factory or get_sessionmaker()
    async with factory() as session:
        while True:
            run_id = (
                await session.execute(
                    select(EvaluationRun.id)
                    .where(EvaluationRun.status == "queued")
                    .order_by(EvaluationRun.created_at, EvaluationRun.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if run_id is None:
                await session.rollback()
                return None

            claimed = await session.execute(
                update(EvaluationRun)
                .where(EvaluationRun.id == run_id, EvaluationRun.status == "queued")
                .values(status="running", started_at=datetime.now(UTC))
            )
            await session.commit()
            if claimed.rowcount == 1:
                return int(run_id)


async def claim_next_proveout_run(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int | None:
    """Optimistically claim the oldest private replay with the evaluation guard pattern."""
    factory = session_factory or get_sessionmaker()
    async with factory() as session:
        while True:
            run_id = (
                await session.execute(
                    select(ProveOutRun.id)
                    .where(ProveOutRun.status == "queued")
                    .order_by(ProveOutRun.created_at, ProveOutRun.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if run_id is None:
                await session.rollback()
                return None

            claimed = await session.execute(
                update(ProveOutRun)
                .where(ProveOutRun.id == run_id, ProveOutRun.status == "queued")
                .values(status="running")
            )
            await session.commit()
            if claimed.rowcount == 1:
                return int(run_id)


async def _load_context(
    run_id: int, factory: async_sessionmaker[AsyncSession]
) -> RunContext:
    async with factory() as session:
        row = (
            await session.execute(
                select(EvaluationRun, Submission, Competition)
                .join(Submission, EvaluationRun.submission_id == Submission.id)
                .join(Competition, Submission.competition_id == Competition.id)
                .where(EvaluationRun.id == run_id)
            )
        ).one()
        run, submission, competition = row
        ruleset = (
            await session.execute(
                select(CompetitionRuleSet).where(
                    CompetitionRuleSet.competition_id == competition.id,
                    CompetitionRuleSet.track == submission.track,
                    CompetitionRuleSet.version == run.rules_version,
                )
            )
        ).scalar_one_or_none()
        if ruleset is None:
            raise RuntimeError(
                f"ruleset {run.rules_version!r} for track {submission.track!r} not found"
            )
        snapshot = dict(run.manifest or {})
        snapshot_parameters = snapshot.get("parameters")
        if isinstance(snapshot_parameters, dict):
            payload = snapshot_parameters.get("submission_payload", submission.payload)
            rules_config = snapshot_parameters.get("rules_config", ruleset.config)
        else:
            payload = submission.payload
            rules_config = ruleset.config
        return RunContext(
            run_id=run.id,
            submission_id=submission.id,
            actor_user_id=submission.user_id,
            competition_slug=competition.slug,
            kind=run.kind,
            rules_version=run.rules_version,
            payload=dict(payload),
            rules_config=dict(rules_config),
            manifest=snapshot or None,
        )


async def _seed_rows(
    context: RunContext, factory: async_sessionmaker[AsyncSession]
) -> list[tuple[int, str, int]]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(EvaluationSeedRun)
                .where(EvaluationSeedRun.evaluation_run_id == context.run_id)
                .order_by(EvaluationSeedRun.id)
            )
        ).scalars().all()
        if not rows:
            count = int(context.rules_config.get(f"{context.kind}_seeds", 0))
            rows = [
                EvaluationSeedRun(
                    evaluation_run_id=context.run_id,
                    seed_label=label,
                    attempt=1,
                    status="queued",
                    metrics={},
                )
                for label in seed_labels(context.kind, count)
            ]
            session.add_all(rows)
            await session.flush()
            result = [(row.id, row.seed_label, row.attempt) for row in rows]
            await session.commit()
            return result
        return [(row.id, row.seed_label, row.attempt) for row in rows]


def _derive_labeled_seed(context: RunContext, label: str) -> int:
    try:
        kind, raw_index = label.rsplit("-", 1)
        index = int(raw_index)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid evaluation seed label: {label!r}") from exc
    round_token = str(context.rules_config.get("round") or DEFAULT_ROUND)
    return derive_seed(context.competition_slug, kind, index, round_token)


def _numeric_metrics(value: Any, prefix: str = "") -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            name = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_numeric_metrics(value[key], name))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math_is_finite(number):
            rows.append((prefix[:100], number))
    return rows


def math_is_finite(value: float) -> bool:
    # Local helper keeps JSON/DB writes free of NaN and infinity without a numpy dependency.
    return value == value and value not in (float("inf"), float("-inf"))


async def _mark_seed_running(
    seed_run_id: int, attempt: int, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as session:
        await session.execute(
            update(EvaluationSeedRun)
            .where(EvaluationSeedRun.id == seed_run_id)
            .values(status="running", attempt=attempt)
        )
        await session.commit()


async def _persist_seed_result(
    context: RunContext,
    seed_run_id: int,
    seed_label: str,
    result: SeedScore,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        await session.execute(
            update(EvaluationSeedRun)
            .where(EvaluationSeedRun.id == seed_run_id)
            .values(status=result.status, score=result.score, metrics=result.metrics)
        )
        numeric = dict(_numeric_metrics(result.metrics))
        numeric["score"] = result.score
        session.add_all(
            EvaluationMetric(
                evaluation_run_id=context.run_id,
                seed_label=seed_label,
                name=name,
                value=value,
            )
            for name, value in sorted(numeric.items())
        )
        await session.commit()


async def _fail_run(
    run_id: int,
    reason: str,
    factory: async_sessionmaker[AsyncSession],
    *,
    seed_run_id: int | None = None,
    attempt: int | None = None,
) -> None:
    summary = {"reason": reason[:1000]}
    async with factory() as session:
        if seed_run_id is not None:
            values: dict[str, Any] = {
                "status": "infra_failure",
                "score": None,
                "metrics": summary,
            }
            if attempt is not None:
                values["attempt"] = attempt
            await session.execute(
                update(EvaluationSeedRun)
                .where(EvaluationSeedRun.id == seed_run_id)
                .values(**values)
            )
        await session.execute(
            update(EvaluationRun)
            .where(EvaluationRun.id == run_id, EvaluationRun.status == "running")
            .values(status="failed", score=None, summary=summary, finished_at=datetime.now(UTC))
        )
        await session.commit()


async def _execute_seed(
    context: RunContext,
    seed_run_id: int,
    seed_label: str,
    initial_attempt: int,
    factory: async_sessionmaker[AsyncSession],
) -> bool:
    # The derived value remains in this stack frame and is never persisted or logged.
    seed = _derive_labeled_seed(context, seed_label)
    seed_hours = float(context.rules_config["seed_hours"])
    window_sec = float(context.rules_config["window_sec"])
    first_attempt = min(MAX_SEED_ATTEMPTS, max(1, int(initial_attempt)))

    for attempt in range(first_attempt, MAX_SEED_ATTEMPTS + 1):
        await _mark_seed_running(seed_run_id, attempt, factory)
        try:
            result = await asyncio.to_thread(
                score_seed,
                context.payload,
                seed,
                seed_hours=seed_hours,
                window_sec=window_sec,
            )
        except Exception as exc:
            if attempt < MAX_SEED_ATTEMPTS:
                continue
            reason = (
                f"infrastructure failure after {MAX_SEED_ATTEMPTS} attempts: "
                f"{type(exc).__name__}"
            )
            await _fail_run(
                context.run_id,
                reason,
                factory,
                seed_run_id=seed_run_id,
                attempt=attempt,
            )
            return False

        await _persist_seed_result(context, seed_run_id, seed_label, result, factory)
        return True
    return False


async def _finalize_scored_run(
    context: RunContext, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as session:
        seeds = (
            await session.execute(
                select(EvaluationSeedRun)
                .where(EvaluationSeedRun.evaluation_run_id == context.run_id)
                .order_by(EvaluationSeedRun.id)
            )
        ).scalars().all()
        scored = [
            float(seed.score)
            for seed in seeds
            if seed.status in ("ok", "participant_failure") and seed.score is not None
        ]
        if not scored:
            raise RuntimeError("evaluation produced no scored seeds")

        participant_failures = sum(seed.status == "participant_failure" for seed in seeds)
        run_score = float(median(scored))
        if participant_failures > 1:
            summary = {
                "excluded": True,
                "reason": "more than one participant failure",
            }
        else:
            summary = {
                "excluded": False,
                "ok_seeds": sum(seed.status == "ok" for seed in seeds),
                "participant_failures": participant_failures,
            }

        evidence = {
            "manifest": context.manifest,
            "result": {
                "evaluation_run_id": context.run_id,
                "kind": context.kind,
                "rules_version": context.rules_version,
                "score": run_score,
                "summary": summary,
            },
            "seed_runs": [
                {
                    "seed_label": seed.seed_label,
                    "attempt": seed.attempt,
                    "status": seed.status,
                    "score": seed.score,
                    "metrics": seed.metrics,
                }
                for seed in seeds
            ],
        }

        await session.execute(
            update(EvaluationRun)
            .where(EvaluationRun.id == context.run_id, EvaluationRun.status == "running")
            .values(
                status="scored",
                score=run_score,
                summary=summary,
                evidence=evidence,
                evidence_sha256=content_sha256(evidence),
                finished_at=datetime.now(UTC),
            )
        )
        session.add(
            EvaluationMetric(
                evaluation_run_id=context.run_id,
                seed_label=None,
                name="score",
                value=run_score,
            )
        )
        session.add(
            AuditEvent(
                actor_user_id=context.actor_user_id,
                action="evaluation.scored",
                entity_type="evaluation_run",
                entity_id=context.run_id,
                payload={"score": run_score, "excluded": summary["excluded"]},
            )
        )
        await session.commit()


async def execute_run(
    run_id: int, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> None:
    factory = session_factory or get_sessionmaker()
    context = await _load_context(run_id, factory)
    for seed_run_id, seed_label, initial_attempt in await _seed_rows(context, factory):
        completed = await _execute_seed(
            context,
            seed_run_id,
            seed_label,
            initial_attempt,
            factory,
        )
        if not completed:
            return
    await _finalize_scored_run(context, factory)


async def execute_proveout_run(
    run_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    factory = session_factory or get_sessionmaker()
    async with factory() as session:
        run = await session.get(ProveOutRun, run_id)
        if run is None or run.status != "running":
            raise RuntimeError(f"claimed prove-out run {run_id} is not running")
        actor_user_id = run.user_id
        endowment = dict(run.endowment)
        window_start = run.window_start
        window_end = run.window_end
        strategy = dict(run.strategy)

    execution = await asyncio.to_thread(
        run_proveout_execution,
        endowment,
        window_start,
        window_end,
        strategy,
    )
    report = execution.report
    manifest = execution.manifest.model_dump(mode="json")
    evidence = execution.evidence
    async with factory() as session:
        completed = await session.execute(
            update(ProveOutRun)
            .where(ProveOutRun.id == run_id, ProveOutRun.status == "running")
            .values(
                status="done",
                report=report,
                manifest=manifest,
                manifest_sha256=content_sha256(manifest),
                evidence=evidence,
                evidence_sha256=content_sha256(evidence),
                error=None,
                finished_at=datetime.now(UTC),
            )
        )
        if completed.rowcount != 1:
            raise RuntimeError(f"prove-out run {run_id} lost its running claim")
        session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action="proveout.completed",
                entity_type="prove_out_run",
                entity_id=run_id,
                payload={
                    "pnl_usd": report["pnl_usd"],
                    "spread_capture_pct": report["spread_capture_pct"],
                },
            )
        )
        await session.commit()


async def _fail_proveout_run(
    run_id: int,
    reason: str,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        await session.execute(
            update(ProveOutRun)
            .where(ProveOutRun.id == run_id, ProveOutRun.status == "running")
            .values(
                status="failed",
                report=None,
                error=reason[:4000],
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def run_worker(
    *, once: bool = False, stop_event: asyncio.Event | None = None
) -> None:
    stop = stop_event or asyncio.Event()
    factory = get_sessionmaker()
    while not stop.is_set():
        run_id = await claim_next_run(factory)
        if run_id is not None:
            try:
                await execute_run(run_id, factory)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("evaluation run %s failed outside seed execution", run_id)
                reason = f"worker infrastructure failure: {type(exc).__name__}: {exc}"
                await _fail_run(run_id, reason, factory)
            if once:
                return
            continue
        proveout_run_id = await claim_next_proveout_run(factory)
        if proveout_run_id is not None:
            try:
                await execute_proveout_run(proveout_run_id, factory)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("prove-out run %s failed", proveout_run_id)
                reason = f"worker failure: {type(exc).__name__}: {exc}"
                await _fail_proveout_run(proveout_run_id, reason, factory)
            if once:
                return
            continue
        if once:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=get_settings().evaluation_poll_sec)
        except TimeoutError:
            pass


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - non-POSIX event loops
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))


async def _async_main(*, once: bool) -> None:
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    await run_worker(once=once, stop_event=stop_event)


def main() -> None:
    parser = argparse.ArgumentParser(description="EFlux official evaluation worker")
    parser.add_argument("--once", action="store_true", help="claim at most one run, then exit")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_async_main(once=args.once))


if __name__ == "__main__":
    main()
