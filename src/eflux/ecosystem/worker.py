"""Worker for ecosystem evaluation and dataset-to-agent jobs.

The API only queues trusted platform work. This worker never executes a user
command, container, plugin, or arbitrary Python entrypoint.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eflux.config import PROJECT_ROOT, get_settings
from eflux.datasets.training import train_behavior_clone
from eflux.db.models import (
    AgentRelease,
    BehaviorDataset,
    DatasetTrainingRun,
    ReleaseEvaluation,
)
from eflux.db.session import get_sessionmaker
from eflux.ecosystem import service
from eflux.ecosystem.catalog import get_standard_profile

log = logging.getLogger(__name__)
TRAINING_ARTIFACTS_BASE = PROJECT_ROOT / "artifacts" / "training_runs"


@dataclass(frozen=True, slots=True)
class EcosystemJob:
    kind: Literal["evaluation", "training"]
    id: int


async def claim_next_ecosystem_job(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> EcosystemJob | None:
    """Claim one oldest queued job with guarded updates that work on SQLite/Postgres."""

    factory = session_factory or get_sessionmaker()
    async with factory() as session:
        candidates: list[tuple[datetime, str, int]] = []
        evaluation = (
            await session.execute(
                select(ReleaseEvaluation.created_at, ReleaseEvaluation.id)
                .where(ReleaseEvaluation.status == "queued")
                .order_by(ReleaseEvaluation.created_at, ReleaseEvaluation.id)
                .limit(1)
            )
        ).one_or_none()
        training = (
            await session.execute(
                select(DatasetTrainingRun.created_at, DatasetTrainingRun.id)
                .where(DatasetTrainingRun.status == "queued")
                .order_by(DatasetTrainingRun.created_at, DatasetTrainingRun.id)
                .limit(1)
            )
        ).one_or_none()
        if evaluation is not None:
            candidates.append((evaluation.created_at, "evaluation", int(evaluation.id)))
        if training is not None:
            candidates.append((training.created_at, "training", int(training.id)))
        for _, kind, job_id in sorted(candidates):
            model = ReleaseEvaluation if kind == "evaluation" else DatasetTrainingRun
            claimed = await session.execute(
                update(model)
                .where(model.id == job_id, model.status == "queued")
                .values(status="running", started_at=datetime.now(UTC))
            )
            await session.commit()
            if claimed.rowcount == 1:
                return EcosystemJob(kind=kind, id=job_id)  # type: ignore[arg-type]
        return None


def _training_output_path(run_id: int, filename: str) -> Path:
    path = (TRAINING_ARTIFACTS_BASE / f"run-{run_id}" / filename).resolve()
    if not path.is_relative_to(TRAINING_ARTIFACTS_BASE.resolve()):
        raise RuntimeError("training output escaped its artifact directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _relative_artifact_path(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _repository_git_commit() -> str | None:
    """Resolve the running source revision without invoking a user-controlled command."""

    configured = os.environ.get("EFLUX_GIT_COMMIT", "").strip()
    if configured:
        return configured
    git_dir = PROJECT_ROOT / ".git"
    head = git_dir / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value.startswith("ref: "):
        return value
    ref = value.removeprefix("ref: ").strip()
    try:
        return (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        try:
            lines = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            if not line.startswith("#") and line.endswith(f" {ref}"):
                return line.split(" ", 1)[0]
    return None


def _checkpoint_from_release(release: AgentRelease) -> Path:
    raw = release.state.get("checkpoint_path")
    expected = release.state.get("checkpoint_sha256")
    if not isinstance(raw, str) or not isinstance(expected, str):
        raise ValueError("warm-start release has no platform checkpoint")
    path = (PROJECT_ROOT / raw).resolve()
    allowed = (PROJECT_ROOT / "checkpoints").resolve(), TRAINING_ARTIFACTS_BASE.resolve()
    if path.is_absolute() and not any(path.is_relative_to(base) for base in allowed):
        raise ValueError("warm-start checkpoint is outside trusted artifact roots")
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("warm-start checkpoint is missing or has changed")
    return path


async def _resolve_warm_start(session: AsyncSession, config: dict[str, Any], owner_id: int) -> Path:
    release_id = config.get("warm_start_release_id")
    training_run_id = config.get("warm_start_training_run_id")
    if training_run_id is not None:
        prior = await session.get(DatasetTrainingRun, int(training_run_id))
        if prior is None or prior.owner_id != owner_id or prior.status != "succeeded":
            raise ValueError("warm-start training run is unavailable")
        release_id = prior.output_release_id
    release = await session.get(AgentRelease, int(release_id)) if release_id is not None else None
    if release is None or release.owner_id != owner_id:
        raise ValueError("warm-start release is unavailable")
    return _checkpoint_from_release(release)


def _derived_release(
    *,
    run: DatasetTrainingRun,
    dataset: BehaviorDataset,
    metrics: dict[str, Any],
    checkpoint: Path,
    algorithm: str,
) -> AgentRelease:
    configured_name = str(run.config.get("release_name") or "").strip()
    name = configured_name or f"{dataset.name} Derived"
    version = str(run.config.get("release_version") or f"training-{run.id}")
    recipe: dict[str, Any] = {
        # A BC checkpoint shares the PPO actor layout and is served through the
        # same fail-closed runtime; online learning stays disabled until PPO fine-tuning.
        "algorithm": "ppo",
        "training_method": algorithm,
        "protocol_version": "2",
        "observation_schema_version": str(metrics["observation_version"]),
        "action_schema_version": str(metrics["encoding_version"]),
        "action_profile": metrics["action_profile"],
        "online_learning": algorithm == "ppo_finetune",
        "fallback_strategy": "safe_hold",
        "risk_limits": {
            "max_open_orders": 20,
            "max_new_orders_per_decision": 5,
            "credit_limit_usd": 10_000,
        },
        "order_routing": {
            "markets": [dataset.market],
            "default_route": "auto",
        },
        "source_dataset_id": dataset.id,
        "source_training_run_id": run.id,
    }
    if algorithm == "ppo_finetune":
        recipe["online_learning_params"] = {
            "sandbox_intervals": int(run.config.get("sandbox_intervals", 288)),
            "hidden_safety_gate": True,
        }
    environment: dict[str, Any] = {
        "runtime": "eflux-managed",
        "agent_protocol_version": 2,
        "dependencies_locked": True,
    }
    git_commit = str(run.config.get("git_commit") or _repository_git_commit() or "").strip()
    if git_commit:
        environment["git_commit"] = git_commit
    else:
        environment["runtime_identity_pending"] = True
    return AgentRelease(
        owner_id=run.owner_id,
        name=name[:100],
        version=version[:64],
        description=f"Platform-derived from Behavior Dataset {dataset.id} via {algorithm}.",
        market=dataset.market,
        visibility="private",
        status="draft",
        recipe=recipe,
        state={
            "checkpoint_path": _relative_artifact_path(checkpoint),
            "checkpoint_sha256": metrics["checkpoint_sha256"],
            "checkpoint_size_bytes": metrics["checkpoint_size_bytes"],
        },
        compatibility={
            "market": dataset.market,
            "requires_retraining": False,
            "profile_id": str(run.config.get("profile_id") or "battery-only"),
        },
        environment=environment,
        badges=["Reproducible", "Online-Adaptive"]
        if algorithm == "ppo_finetune"
        else ["Reproducible"],
        parent_release_id=None,
    )


def _ppo_finetune(
    warm_start: Path,
    output: Path,
    *,
    market: str,
    profile_id: str,
    intervals: int,
    seed: int,
) -> dict[str, Any]:
    from eflux.agents.bench.run import measure_episode, run_episode
    from eflux.agents.hybrid import StrategyAgent
    from eflux.agents.ppo.bc import checkpoint_meta
    from eflux.agents.ppo.online_ppo import build_online_policy
    from eflux.vpp.base import VPPParams

    profile = get_standard_profile(profile_id)
    params = VPPParams.from_dict(profile["spec"]["vpp_params"])
    policy = build_online_policy(str(warm_start), learning=True, auto_update=True, seed=seed)
    sim, vpp = run_episode(
        lambda: StrategyAgent(policy=policy, use_forecast=True),
        n_ticks=intervals,
        tick_h=5.0 / 60.0,
        episode_seed=seed,
        candidate_params=params,
        market_price_ref=Decimal("50"),
        market_mode=market,
    )
    train_metrics = measure_episode("ppo-finetune", sim, vpp, intervals)
    policy.save(str(output))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    meta = checkpoint_meta(str(warm_start))

    # Hidden, non-learning safety episode. The seed is platform-derived and not supplied by users.
    hidden_seed = int(hashlib.sha256(digest.encode()).hexdigest()[:8], 16)
    frozen = build_online_policy(str(output), learning=False, seed=hidden_seed)
    hidden_sim, hidden_vpp = run_episode(
        lambda: StrategyAgent(policy=frozen, use_forecast=True),
        n_ticks=intervals,
        tick_h=5.0 / 60.0,
        episode_seed=hidden_seed,
        candidate_params=params,
        market_price_ref=Decimal("50"),
        market_mode=market,
    )
    hidden = measure_episode("ppo-hidden", hidden_sim, hidden_vpp, intervals)
    hidden_rejection_rate = hidden.risk_rejections / max(1, intervals)
    if hidden_rejection_rate > 0.25:
        raise RuntimeError(
            f"risk gate failed: hidden rejection rate {hidden_rejection_rate:.3f} exceeds 0.25"
        )
    return {
        "samples": intervals,
        "epochs": intervals,
        "seed": seed,
        "mode_accuracy": None,
        "trade_mode_accuracy": None,
        "per_mode_recall": {},
        "checkpoint_path": str(output),
        "checkpoint_sha256": digest,
        "checkpoint_size_bytes": output.stat().st_size,
        "encoding_version": int(meta.get("encoding_version", 2)),
        "observation_version": int(meta.get("obs_version", 4)),
        "action_profile": str(
            meta.get("action_profile", "realprice_grid" if market == "realprice" else "p2p")
        ),
        "observation_dim": int(meta.get("obs_dim", 33)),
        "action_dim": int(policy.learner.net.action_dim),
        "sandbox": {
            "realized_pnl_usd": train_metrics.realized_pnl,
            "mark_to_market_usd": train_metrics.mark_to_market,
            "risk_rejections": train_metrics.risk_rejections,
        },
        "hidden_evaluation": {
            "realized_pnl_usd": hidden.realized_pnl,
            "mark_to_market_usd": hidden.mark_to_market,
            "imbalance_kwh": hidden.unresolved_imbalance_kwh,
            "risk_rejections": hidden.risk_rejections,
            "rejection_rate": hidden_rejection_rate,
            "passed_risk_gate": True,
        },
    }


async def execute_training_job(
    run_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    factory = session_factory or get_sessionmaker()
    async with factory() as session:
        run = await session.get(DatasetTrainingRun, run_id)
        if run is None or run.status != "running":
            raise RuntimeError("training job is not claimed")
        dataset = await session.get(BehaviorDataset, run.dataset_id)
        if dataset is None:
            raise RuntimeError("training dataset disappeared")
        artifact = service.dataset_artifact_path(dataset)
        config = dict(run.config or {})
        epochs = min(10_000, max(1, int(config.get("epochs", 100))))
        seed = int(config.get("seed", 0))
        output = _training_output_path(
            run.id, "bc-warm-start.pt" if run.algorithm == "bc_warm_start" else "ppo-finetuned.pt"
        )
        warm_start = (
            await _resolve_warm_start(session, config, run.owner_id)
            if run.algorithm == "ppo_finetune"
            else None
        )

    try:
        if run.algorithm == "bc_warm_start":
            metrics = await asyncio.to_thread(
                train_behavior_clone,
                artifact,
                output,
                epochs=epochs,
                seed=seed,
                market_mode=dataset.market,
            )
        else:
            assert warm_start is not None
            market = dataset.market
            metrics = await asyncio.to_thread(
                _ppo_finetune,
                warm_start,
                output,
                market=market,
                profile_id=str(config.get("profile_id") or "battery-only"),
                intervals=min(2016, max(24, int(config.get("sandbox_intervals", 288)))),
                seed=seed,
            )
        async with factory() as session:
            current = await session.get(DatasetTrainingRun, run_id)
            dataset = await session.get(BehaviorDataset, run.dataset_id)
            if current is None or dataset is None:
                raise RuntimeError("training job disappeared before persistence")
            release = _derived_release(
                run=current,
                dataset=dataset,
                metrics=metrics,
                checkpoint=output,
                algorithm=current.algorithm,
            )
            session.add(release)
            await session.flush()
            current.status = "succeeded"
            current.metrics = metrics
            current.output_release_id = release.id
            current.finished_at = datetime.now(UTC)
            await session.commit()
    except Exception as exc:
        async with factory() as session:
            await session.execute(
                update(DatasetTrainingRun)
                .where(DatasetTrainingRun.id == run_id)
                .values(
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}"[:4000],
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()
        raise


async def execute_ecosystem_job(
    job: EcosystemJob,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    if job.kind == "training":
        await execute_training_job(job.id, session_factory)
        return
    # Release evaluation is implemented below the same safe runtime boundary.
    from eflux.ecosystem.evaluation import execute_release_evaluation

    await execute_release_evaluation(job.id, session_factory)


async def run_worker(*, once: bool = False, stop_event: asyncio.Event | None = None) -> None:
    stop = stop_event or asyncio.Event()
    factory = get_sessionmaker()
    while not stop.is_set():
        job = await claim_next_ecosystem_job(factory)
        if job is not None:
            try:
                await execute_ecosystem_job(job, factory)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ecosystem %s job %s failed", job.kind, job.id)
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
        except NotImplementedError:  # pragma: no cover
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))


async def _async_main(*, once: bool) -> None:
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    await run_worker(once=once, stop_event=stop)


def main() -> None:
    parser = argparse.ArgumentParser(description="EFlux ecosystem job worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, get_settings().log_level.upper(), logging.INFO))
    asyncio.run(_async_main(once=args.once))


if __name__ == "__main__":
    main()
