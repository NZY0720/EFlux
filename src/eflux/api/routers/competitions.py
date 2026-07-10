"""Public competition catalogue endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from eflux.api.deps import CurrentUser, DbSession
from eflux.config import get_settings
from eflux.db.models import (
    AuditEvent,
    Competition,
    CompetitionRuleSet,
    EvaluationRun,
    EvaluationSeedRun,
    Submission,
    User,
)
from eflux.evaluation.seeds import seed_labels, seed_values
from eflux.simulator.scenarios import MANAGED_ALGORITHMS

router = APIRouter(prefix="/competitions", tags=["competitions"])
submissions_router = APIRouter(prefix="/submissions", tags=["competitions"])


class CompetitionListOut(BaseModel):
    id: int
    slug: str
    title: str
    status: str
    tracks: list[str]
    submission_counts: dict[str, int]


class CompetitionRuleSetOut(BaseModel):
    id: int
    version: str
    track: str
    config: dict
    created_at: datetime


class CompetitionDetailOut(CompetitionListOut):
    description: str
    rulesets: list[CompetitionRuleSetOut]
    practice_seed_values: list[int]
    hidden_seed_count: int
    holdout_seed_count: int


class ManagedSubmissionPayload(BaseModel):
    algorithm: str = Field(min_length=1, max_length=64)
    llm_enabled: bool
    preset: str | None = Field(default=None, min_length=1, max_length=100)
    endowment: dict[str, object] | None = None
    risk: object | None = None

    @model_validator(mode="after")
    def _has_preset_or_endowment(self) -> ManagedSubmissionPayload:
        if self.preset is None and self.endowment is None:
            raise ValueError("one of preset or endowment is required")
        return self


class SubmissionCreateIn(BaseModel):
    track: Literal["managed"]
    payload: ManagedSubmissionPayload


class SubmissionOut(BaseModel):
    id: int
    competition_id: int
    track: str
    status: str
    payload: dict
    created_at: datetime
    updated_at: datetime


class EvaluationSeedRunOut(BaseModel):
    seed_label: str
    attempt: int
    status: str
    score: float | None


class EvaluationRunOut(BaseModel):
    id: int
    status: str
    rules_version: str
    score: float | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    seed_runs: list[EvaluationSeedRunOut]


class SubmissionDetailOut(SubmissionOut):
    latest_run: EvaluationRunOut | None


class LeaderboardEntryOut(BaseModel):
    rank: int
    submission_id: int
    user_email: str
    algorithm: str
    score: float
    seed_ok_count: int
    seed_failed_count: int


class LeaderboardOut(BaseModel):
    competition_slug: str
    entries: list[LeaderboardEntryOut]


def _competition_counts(rows: list[tuple[int, str, int]]) -> dict[int, dict[str, int]]:
    counts: dict[int, dict[str, int]] = {}
    for competition_id, track, count in rows:
        counts.setdefault(competition_id, {})[track] = count
    return counts


async def _managed_ruleset(
    session: DbSession, competition_id: int
) -> CompetitionRuleSet | None:
    return (
        await session.execute(
            select(CompetitionRuleSet)
            .where(
                CompetitionRuleSet.competition_id == competition_id,
                CompetitionRuleSet.track == "managed",
            )
            .order_by(CompetitionRuleSet.created_at.desc(), CompetitionRuleSet.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _seed_count(ruleset: CompetitionRuleSet, kind: str) -> int:
    value = ruleset.config.get(f"{kind}_seeds", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _round_token(ruleset: CompetitionRuleSet) -> str:
    round_token = ruleset.config.get("round", "round-1")
    return round_token if isinstance(round_token, str) else "round-1"


def _is_admin(user: User) -> bool:
    return user.role == "admin" or user.email.strip().lower() in get_settings().admin_email_set


def _submission_out(submission: Submission) -> SubmissionOut:
    return SubmissionOut(
        id=submission.id,
        competition_id=submission.competition_id,
        track=submission.track,
        status=submission.status,
        payload=submission.payload,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


def _mask_email(email: str) -> str:
    local, sep, domain = email.partition("@")
    if not sep:
        return f"{local[:2]}***"
    return f"{local[:2]}***@{domain}"


@router.get("", response_model=list[CompetitionListOut])
async def list_competitions(session: DbSession) -> list[CompetitionListOut]:
    competitions = (
        await session.execute(
            select(Competition)
            .where(Competition.status.in_(("open", "closed")))
            .order_by(Competition.created_at.desc(), Competition.id.desc())
        )
    ).scalars().all()
    if not competitions:
        return []

    competition_ids = [competition.id for competition in competitions]
    tracks = (
        await session.execute(
            select(CompetitionRuleSet.competition_id, CompetitionRuleSet.track)
            .where(CompetitionRuleSet.competition_id.in_(competition_ids))
            .distinct()
            .order_by(CompetitionRuleSet.track)
        )
    ).all()
    count_rows = (
        await session.execute(
            select(Submission.competition_id, Submission.track, func.count(Submission.id))
            .where(Submission.competition_id.in_(competition_ids))
            .group_by(Submission.competition_id, Submission.track)
        )
    ).all()
    tracks_by_competition: dict[int, list[str]] = {}
    for competition_id, track in tracks:
        tracks_by_competition.setdefault(competition_id, []).append(track)
    counts_by_competition = _competition_counts(count_rows)
    return [
        CompetitionListOut(
            id=competition.id,
            slug=competition.slug,
            title=competition.title,
            status=competition.status,
            tracks=tracks_by_competition.get(competition.id, []),
            submission_counts=counts_by_competition.get(competition.id, {}),
        )
        for competition in competitions
    ]


@router.get("/{slug}", response_model=CompetitionDetailOut)
async def get_competition(slug: str, session: DbSession) -> CompetitionDetailOut:
    competition = (
        await session.execute(select(Competition).where(Competition.slug == slug))
    ).scalar_one_or_none()
    if competition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "competition not found")

    rulesets = (
        await session.execute(
            select(CompetitionRuleSet)
            .where(CompetitionRuleSet.competition_id == competition.id)
            .order_by(CompetitionRuleSet.track, CompetitionRuleSet.version)
        )
    ).scalars().all()
    count_rows = (
        await session.execute(
            select(Submission.competition_id, Submission.track, func.count(Submission.id))
            .where(Submission.competition_id == competition.id)
            .group_by(Submission.competition_id, Submission.track)
        )
    ).all()
    counts = _competition_counts(count_rows).get(competition.id, {})
    tracks = sorted({ruleset.track for ruleset in rulesets})
    managed_ruleset = await _managed_ruleset(session, competition.id)
    practice_count = _seed_count(managed_ruleset, "practice") if managed_ruleset else 0
    hidden_count = _seed_count(managed_ruleset, "hidden") if managed_ruleset else 0
    holdout_count = _seed_count(managed_ruleset, "holdout") if managed_ruleset else 0
    round_token = _round_token(managed_ruleset) if managed_ruleset else "round-1"
    return CompetitionDetailOut(
        id=competition.id,
        slug=competition.slug,
        title=competition.title,
        description=competition.description,
        status=competition.status,
        tracks=tracks,
        submission_counts=counts,
        rulesets=[
            CompetitionRuleSetOut(
                id=ruleset.id,
                version=ruleset.version,
                track=ruleset.track,
                config=ruleset.config,
                created_at=ruleset.created_at,
            )
            for ruleset in rulesets
        ],
        practice_seed_values=seed_values(slug, "practice", practice_count, round_token),
        hidden_seed_count=hidden_count,
        holdout_seed_count=holdout_count,
    )


@router.post("/{slug}/submissions", response_model=SubmissionOut, status_code=status.HTTP_201_CREATED)
async def create_submission(
    slug: str,
    body: SubmissionCreateIn,
    session: DbSession,
    user: CurrentUser,
) -> SubmissionOut:
    competition = (
        await session.execute(select(Competition).where(Competition.slug == slug))
    ).scalar_one_or_none()
    if competition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "competition not found")
    if competition.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "competition is not open")
    ruleset = await _managed_ruleset(session, competition.id)
    if ruleset is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "managed ruleset not found")
    if body.payload.algorithm not in MANAGED_ALGORITHMS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unknown managed algorithm {body.payload.algorithm!r}; choose from {list(MANAGED_ALGORITHMS)}",
        )
    if body.payload.llm_enabled:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "LLM agents compete in the live sandbox until the Model track (Phase C); managed official evaluations require llm_enabled=false",
        )

    today = datetime.now(UTC).date()
    created_today = (
        await session.execute(
            select(Submission.created_at).where(
                Submission.competition_id == competition.id,
                Submission.user_id == user.id,
                Submission.track == body.track,
            )
        )
    ).scalars().all()
    daily_limit = ruleset.config.get("submissions_per_day", 0)
    daily_limit = daily_limit if isinstance(daily_limit, int) and daily_limit >= 0 else 0
    if sum(created_at.date() == today for created_at in created_today) >= daily_limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "daily submission cooldown exceeded")

    submission = Submission(
        competition_id=competition.id,
        user_id=user.id,
        track=body.track,
        status="finalized",
        payload=body.payload.model_dump(exclude_none=True),
    )
    session.add(submission)
    await session.flush()
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="submission.created",
            entity_type="submission",
            entity_id=submission.id,
            payload={"competition_slug": competition.slug, "track": submission.track},
        )
    )
    return _submission_out(submission)


@submissions_router.post("/{submission_id}/evaluate", response_model=EvaluationRunOut, status_code=status.HTTP_201_CREATED)
async def enqueue_evaluation(
    submission_id: int,
    session: DbSession,
    user: CurrentUser,
) -> EvaluationRunOut:
    submission = (
        await session.execute(select(Submission).where(Submission.id == submission_id))
    ).scalar_one_or_none()
    if submission is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submission not found")
    if submission.user_id != user.id and not _is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "submission is not yours")
    active_run = (
        await session.execute(
            select(EvaluationRun.id).where(
                EvaluationRun.submission_id == submission.id,
                EvaluationRun.status.in_(("queued", "running")),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if active_run is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "an evaluation is already queued or running")
    ruleset = await _managed_ruleset(session, submission.competition_id)
    if ruleset is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "managed ruleset not found")

    run = EvaluationRun(status="queued", rules_version=ruleset.version, submission_id=submission.id)
    session.add(run)
    await session.flush()
    for label in seed_labels("hidden", _seed_count(ruleset, "hidden")):
        session.add(
            EvaluationSeedRun(
                evaluation_run_id=run.id,
                seed_label=label,
                attempt=1,
                status="queued",
            )
        )
    await session.flush()
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="evaluation.enqueued",
            entity_type="evaluation_run",
            entity_id=run.id,
            payload={"submission_id": submission.id, "rules_version": ruleset.version},
        )
    )
    return EvaluationRunOut(
        id=run.id,
        status=run.status,
        rules_version=run.rules_version,
        score=run.score,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        seed_runs=[
            EvaluationSeedRunOut(seed_label=label, attempt=1, status="queued", score=None)
            for label in seed_labels("hidden", _seed_count(ruleset, "hidden"))
        ],
    )


@submissions_router.get("/{submission_id}", response_model=SubmissionDetailOut)
async def get_submission(
    submission_id: int,
    session: DbSession,
    user: CurrentUser,
) -> SubmissionDetailOut:
    submission = (
        await session.execute(select(Submission).where(Submission.id == submission_id))
    ).scalar_one_or_none()
    if submission is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "submission not found")
    if submission.user_id != user.id and not _is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "submission is not yours")
    run = (
        await session.execute(
            select(EvaluationRun)
            .where(EvaluationRun.submission_id == submission.id)
            .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_run = None
    if run is not None:
        seed_runs = (
            await session.execute(
                select(EvaluationSeedRun)
                .where(EvaluationSeedRun.evaluation_run_id == run.id)
                .order_by(EvaluationSeedRun.id)
            )
        ).scalars().all()
        latest_run = EvaluationRunOut(
            id=run.id,
            status=run.status,
            rules_version=run.rules_version,
            score=run.score,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            seed_runs=[
                EvaluationSeedRunOut(
                    seed_label=seed_run.seed_label,
                    attempt=seed_run.attempt,
                    status=seed_run.status,
                    score=seed_run.score,
                )
                for seed_run in seed_runs
            ],
        )
    return SubmissionDetailOut(**_submission_out(submission).model_dump(), latest_run=latest_run)


@router.get("/{slug}/leaderboard", response_model=LeaderboardOut)
async def get_leaderboard(slug: str, session: DbSession) -> LeaderboardOut:
    competition = (
        await session.execute(select(Competition).where(Competition.slug == slug))
    ).scalar_one_or_none()
    if competition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "competition not found")
    rows = (
        await session.execute(
            select(EvaluationRun, Submission, User)
            .join(Submission, EvaluationRun.submission_id == Submission.id)
            .join(User, Submission.user_id == User.id)
            .where(
                Submission.competition_id == competition.id,
                Submission.track == "managed",
                EvaluationRun.score.is_not(None),
            )
            .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
        )
    ).all()
    latest_scored: list[tuple[EvaluationRun, Submission, User]] = []
    seen_submission_ids: set[int] = set()
    for run, submission, owner in rows:
        if submission.id in seen_submission_ids or run.summary.get("excluded") is True:
            continue
        seen_submission_ids.add(submission.id)
        latest_scored.append((run, submission, owner))
    latest_scored.sort(key=lambda row: row[0].score or float("-inf"), reverse=True)

    entries: list[LeaderboardEntryOut] = []
    for rank, (run, submission, owner) in enumerate(latest_scored, start=1):
        seed_statuses = (
            await session.execute(
                select(EvaluationSeedRun.status).where(EvaluationSeedRun.evaluation_run_id == run.id)
            )
        ).scalars().all()
        entries.append(
            LeaderboardEntryOut(
                rank=rank,
                submission_id=submission.id,
                user_email=_mask_email(owner.email),
                algorithm=str(submission.payload.get("algorithm", "")),
                score=run.score,
                seed_ok_count=sum(s in {"ok", "completed", "succeeded"} for s in seed_statuses),
                seed_failed_count=sum(s == "failed" for s in seed_statuses),
            )
        )
    return LeaderboardOut(competition_slug=competition.slug, entries=entries)
