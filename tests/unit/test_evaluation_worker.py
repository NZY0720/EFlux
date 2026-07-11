from __future__ import annotations

import asyncio
import struct
from types import SimpleNamespace

from sqlalchemy import select

from eflux.agents.base import AgentContext, BaseAgent
from eflux.agents.bench.scenarios import test_slot_params as benchmark_endowment
from eflux.agents.decision import AgentDecision
from eflux.agents.truthful import TruthfulAgent
from eflux.db.models import (
    AuditEvent,
    Competition,
    CompetitionRuleSet,
    EvaluationMetric,
    EvaluationRun,
    EvaluationSeedRun,
    Submission,
    User,
)
from eflux.db.session import get_sessionmaker
from eflux.evaluation import scoring, worker
from eflux.evaluation.scoring import SeedScore, score_seed


async def _queue_runs(db_session, *, count: int, hidden_seeds: int = 1) -> list[int]:
    user = User(email=f"worker-{count}-{hidden_seeds}@example.com")
    competition = Competition(
        slug=f"worker-{count}-{hidden_seeds}",
        title="Worker test",
        description="",
        status="open",
    )
    db_session.add_all([user, competition])
    await db_session.flush()
    db_session.add(
        CompetitionRuleSet(
            competition_id=competition.id,
            version="rules-test",
            track="managed",
            config={
                "window_sec": 60,
                "deadline_ms": 500,
                "hidden_seeds": hidden_seeds,
                "seed_hours": 1 / 60,
                "round": "test-round",
            },
        )
    )
    submission = Submission(
        competition_id=competition.id,
        user_id=user.id,
        track="managed",
        status="finalized",
        payload={
            "algorithm": "truthful",
            "llm_enabled": True,
            "preset": "Solar Trader",
        },
    )
    db_session.add(submission)
    await db_session.flush()
    runs = [
        EvaluationRun(
            submission_id=submission.id,
            status="queued",
            rules_version="rules-test",
            summary={},
        )
        for _ in range(count)
    ]
    db_session.add_all(runs)
    await db_session.commit()
    return [run.id for run in runs]


def test_same_submission_and_seed_are_bit_identical():
    payload = {"algorithm": "truthful", "llm_enabled": True, "preset": "benchmark"}
    first = score_seed(payload, 123456, seed_hours=24, window_sec=600)
    second = score_seed(payload, 123456, seed_hours=24, window_sec=600)

    assert first.status == second.status == "ok"
    assert struct.pack("!d", first.score) == struct.pack("!d", second.score)
    print(f"determinism scores: {first.score!r} {second.score!r}")


async def test_concurrent_claims_never_return_the_same_run(db_session):
    run_ids = await _queue_runs(db_session, count=2)
    factory = get_sessionmaker()

    claimed = await asyncio.gather(worker.claim_next_run(factory), worker.claim_next_run(factory))

    assert set(claimed) == set(run_ids)
    assert len(set(claimed)) == 2
    assert await worker.claim_next_run(factory) is None


class _RaisingAgent(BaseAgent):
    def __init__(self) -> None:
        self.ticks = 0

    def decide(self, ctx: AgentContext):
        self.ticks += 1
        if self.ticks == 2:
            raise RuntimeError("participant boom")
        return AgentDecision.hold("before injected failure")


def test_agent_exception_scores_at_roster_floor(monkeypatch):
    monkeypatch.setattr(scoring, "_make_submission_agent", lambda *args, **kwargs: _RaisingAgent())

    result = score_seed(
        {"algorithm": "truthful", "endowment": benchmark_endowment().to_dict()},
        98765,
        seed_hours=24,
        window_sec=600,
    )

    assert result.status == "participant_failure"
    assert result.score == result.metrics["floor_score"]
    assert "participant boom" in result.metrics["reason"]


async def test_two_participant_failures_exclude_the_scored_run(db_session, monkeypatch):
    (run_id,) = await _queue_runs(db_session, count=1, hidden_seeds=2)

    def participant_failure(*args, **kwargs):
        return SeedScore(
            status="participant_failure",
            score=-1.25,
            metrics={"floor_score": -1.25, "reason": "invalid action"},
        )

    monkeypatch.setattr(worker, "score_seed", participant_failure)
    await worker.run_worker(once=True)

    factory = get_sessionmaker()
    async with factory() as session:
        run = await session.get(EvaluationRun, run_id)
        seeds = (
            (
                await session.execute(
                    select(EvaluationSeedRun).where(EvaluationSeedRun.evaluation_run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
    assert run is not None
    assert run.status == "scored"
    assert run.score == -1.25
    assert run.summary == {"excluded": True, "reason": "more than one participant failure"}
    assert [seed.status for seed in seeds] == ["participant_failure", "participant_failure"]


async def test_infrastructure_failure_retries_three_times_then_fails(db_session, monkeypatch):
    (run_id,) = await _queue_runs(db_session, count=1)
    calls = 0

    def broken_infrastructure(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise OSError("simulator unavailable")

    monkeypatch.setattr(worker, "score_seed", broken_infrastructure)
    await worker.run_worker(once=True)

    factory = get_sessionmaker()
    async with factory() as session:
        run = await session.get(EvaluationRun, run_id)
        seed = (
            await session.execute(
                select(EvaluationSeedRun).where(EvaluationSeedRun.evaluation_run_id == run_id)
            )
        ).scalar_one()
    assert calls == 3
    assert run is not None and run.status == "failed"
    assert seed.status == "infra_failure"
    assert seed.attempt == 3
    assert "infrastructure failure after 3 attempts" in run.summary["reason"]


async def test_once_drains_exactly_one_run_end_to_end(db_session):
    first_id, second_id = await _queue_runs(db_session, count=2)

    await worker.run_worker(once=True)

    factory = get_sessionmaker()
    async with factory() as session:
        first = await session.get(EvaluationRun, first_id)
        second = await session.get(EvaluationRun, second_id)
        audit = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "evaluation.scored",
                    AuditEvent.entity_id == first_id,
                )
            )
        ).scalar_one()
        metrics = (
            (
                await session.execute(
                    select(EvaluationMetric).where(EvaluationMetric.evaluation_run_id == first_id)
                )
            )
            .scalars()
            .all()
        )
    assert first is not None and first.status == "scored"
    assert first.started_at is not None and first.finished_at is not None
    assert second is not None and second.status == "queued"
    assert audit.payload["score"] == first.score
    assert metrics


def test_official_construction_forces_llm_off(monkeypatch):
    captured = {}

    def fake_provision(simulator, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(agent=TruthfulAgent(), llm_enabled=False)

    monkeypatch.setattr(scoring, "provision_managed_vpp", fake_provision)
    scoring._make_submission_agent(
        {"algorithm": "truthful", "llm_enabled": True},
        seed=42,
        endowment=benchmark_endowment(),
    )

    assert captured["llm_enabled"] is False
    assert captured["online_learning"] is False
    assert captured["use_real_weather"] is False
