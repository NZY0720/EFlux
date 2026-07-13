"""Strictly private Prove-out replay queue and report endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from eflux.api.deps import CurrentUser, DbSession
from eflux.config import get_settings
from eflux.db.models import ProveOutRun, User
from eflux.evaluation.proveout import (
    latest_historical_date,
    validate_strategy,
)

router = APIRouter(prefix="/prove-out/runs", tags=["prove-out"])


class BatteryIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    power_mw: float = Field(gt=0)
    energy_mwh: float = Field(gt=0)
    round_trip_efficiency: float = Field(gt=0.5, le=1)
    cycle_cost_per_mwh: float = Field(default=0, ge=0)


class WindIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    power_mw: float = Field(default=0, ge=0)
    mean_speed_mps: float = Field(default=7, gt=0, le=60)


class LoadIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    base_mw: float = Field(default=0, ge=0)
    profile: Literal["residential", "commercial", "industrial", "flat", "ev"] = "commercial"
    flexibility: float = Field(default=0, ge=0, le=1)


class EndowmentIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    battery: BatteryIn | None = None
    solar_mw: float = Field(default=0, ge=0)
    wind: WindIn | None = None
    load: LoadIn | None = None
    cash_usd: float = Field(default=10000, ge=0)

    @model_validator(mode="after")
    def _has_physical_asset(self) -> EndowmentIn:
        wind_mw = 0.0 if self.wind is None else self.wind.power_mw
        load_mw = 0.0 if self.load is None else self.load.base_mw
        if self.battery is None and self.solar_mw == 0 and wind_mw == 0 and load_mw == 0:
            raise ValueError("endowment must include a battery, generation, or load")
        return self


class WindowIn(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _valid_inclusive_span(self) -> WindowIn:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days + 1 > 31:
            raise ValueError("window span must be at most 31 inclusive days")
        latest = latest_historical_date()
        if self.end_date > latest:
            raise ValueError(
                f"end_date must be on or before the latest complete CAISO day, {latest}"
            )
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
    degradation_cost_usd: float = 0
    ending_soc_kwh: float | None = None
    energy_bought_kwh: float | None = None
    energy_sold_kwh: float | None = None
    solar_generation_kwh: float = 0
    wind_generation_kwh: float = 0
    load_consumption_kwh: float = 0
    ledger_breakdown: dict[str, float] = Field(default_factory=dict)
    evidence_id: str | None = None
    engine: str | None = None
    price_resolution: str | None = None
    audit_event_count: int | None = None
    replay_state_sha256: str | None = None
    replay_verified: bool | None = None
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
    manifest: dict[str, Any] | None
    evidence_sha256: str | None
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
        manifest=run.manifest,
        evidence_sha256=run.evidence_sha256,
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
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

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


@router.get("/{run_id}/evidence")
async def download_prove_out_evidence(
    run_id: int,
    session: DbSession,
    user: CurrentUser,
) -> JSONResponse:
    """Download the immutable replay manifest, audit events and settlement ledger."""

    run = await session.get(ProveOutRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "prove-out run not found")
    if run.user_id != user.id and not _is_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "prove-out run is not yours")
    if run.status != "done" or run.evidence is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "prove-out evidence is not ready")
    return JSONResponse(
        content=run.evidence,
        headers={
            "Content-Disposition": f'attachment; filename="prove-out-{run.id}-evidence.json"',
            "X-Evidence-SHA256": run.evidence_sha256 or "",
        },
    )
