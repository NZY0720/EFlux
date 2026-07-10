"""Strictly private Prove-out replay queue and report endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from eflux.api.deps import CurrentUser, DbSession
from eflux.config import get_settings
from eflux.db.models import ProveOutRun, User
from eflux.evaluation.proveout import (
    ProveOutDataError,
    available_price_ranges,
    format_available_ranges,
    validate_strategy,
    window_is_available,
)

router = APIRouter(prefix="/prove-out/runs", tags=["prove-out"])


class BatteryIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    power_mw: float = Field(gt=0)
    energy_mwh: float = Field(gt=0)
    round_trip_efficiency: float = Field(gt=0.5, le=1)
    cycle_cost_per_mwh: float = Field(default=0, ge=0)


class EndowmentIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    battery: BatteryIn | None = None
    solar_mw: float = Field(default=0, ge=0)
    cash_usd: float = 10000


class WindowIn(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _valid_inclusive_span(self) -> WindowIn:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days + 1 > 31:
            raise ValueError("window span must be at most 31 inclusive days")
        return self


class StrategyIn(BaseModel):
    algorithm: str = Field(default="battery_arbitrageur", min_length=1, max_length=64)
    params: dict[str, Any] | None = None


class ProveOutCreateIn(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    endowment: EndowmentIn
    window: WindowIn
    strategy: StrategyIn = Field(default_factory=StrategyIn)


class ProveOutQueuedOut(BaseModel):
    run_id: int
    status: str


class ProveOutListOut(BaseModel):
    run_id: int
    label: str | None
    status: str
    window_start: date
    window_end: date
    created_at: datetime
    pnl_usd: float | None = None
    spread_capture_pct: float | None = None


class DailyReportOut(BaseModel):
    date: date
    pnl_usd: float
    spread_capture_pct: float | None


class ProveOutReportOut(BaseModel):
    pnl_usd: float
    per_kw_month: float
    spread_capture_pct: float | None
    perfect_foresight_usd: float
    baseline_hold_usd: float
    max_drawdown_usd: float
    trades: int
    risk_rejections: int
    imbalance_penalty_usd: float
    days: int
    daily: list[DailyReportOut]


class ProveOutDetailOut(BaseModel):
    run_id: int
    label: str | None
    status: str
    endowment: dict[str, Any]
    window_start: date
    window_end: date
    strategy: dict[str, Any]
    report: ProveOutReportOut | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


def _is_admin(user: User) -> bool:
    return user.role == "admin" or user.email.strip().lower() in get_settings().admin_email_set


def _list_out(run: ProveOutRun) -> ProveOutListOut:
    report = run.report or {}
    return ProveOutListOut(
        run_id=run.id,
        label=run.label,
        status=run.status,
        window_start=run.window_start,
        window_end=run.window_end,
        created_at=run.created_at,
        pnl_usd=report.get("pnl_usd"),
        spread_capture_pct=report.get("spread_capture_pct"),
    )


def _detail_out(run: ProveOutRun) -> ProveOutDetailOut:
    return ProveOutDetailOut(
        run_id=run.id,
        label=run.label,
        status=run.status,
        endowment=run.endowment,
        window_start=run.window_start,
        window_end=run.window_end,
        strategy=run.strategy,
        report=run.report,
        error=run.error,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


@router.post("", response_model=ProveOutQueuedOut, status_code=status.HTTP_202_ACCEPTED)
async def create_prove_out_run(
    body: ProveOutCreateIn,
    session: DbSession,
    user: CurrentUser,
) -> ProveOutQueuedOut:
    strategy = body.strategy.model_dump(exclude_none=True)
    try:
        validate_strategy(strategy)
        ranges = available_price_ranges()
    except (ProveOutDataError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if not window_is_available(
        body.window.start_date,
        body.window.end_date,
        ranges=ranges,
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "requested window is outside cached CAISO price availability; "
            f"available ranges: {format_available_ranges(ranges)}",
        )

    run = ProveOutRun(
        user_id=user.id,
        label=body.label,
        endowment=body.endowment.model_dump(exclude_none=True),
        window_start=body.window.start_date,
        window_end=body.window.end_date,
        strategy=strategy,
        status="queued",
    )
    session.add(run)
    await session.flush()
    return ProveOutQueuedOut(run_id=run.id, status="queued")


@router.get("", response_model=list[ProveOutListOut])
async def list_prove_out_runs(
    session: DbSession,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ProveOutListOut]:
    runs = (
        await session.execute(
            select(ProveOutRun)
            .where(ProveOutRun.user_id == user.id)
            .order_by(ProveOutRun.created_at.desc(), ProveOutRun.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [_list_out(run) for run in runs]


@router.get("/{run_id}", response_model=ProveOutDetailOut)
async def get_prove_out_run(
    run_id: int,
    session: DbSession,
    user: CurrentUser,
) -> ProveOutDetailOut:
    run = (
        await session.execute(select(ProveOutRun).where(ProveOutRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "prove-out run not found")
    if run.user_id != user.id and not _is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "prove-out run is not yours")
    return _detail_out(run)
