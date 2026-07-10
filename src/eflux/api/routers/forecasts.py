"""Public forecast endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from eflux.api.deps import DbSession, ForecastServiceDep
from eflux.config import get_settings
from eflux.db.models import ForecastOutcome
from eflux.forecasting.service import HISTORY_MAXLEN

router = APIRouter(prefix="/forecasts", tags=["forecasts"])

SKILL_TARGETS = ("price_real", "price_p2p")
SKILL_HORIZONS = ("5m", "1h", "12h")


def _utc_time(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive timestamps for Python window checks."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _minute_key(value: datetime) -> datetime:
    """Round to the nearest minute: the refresh grid drifts sub-second per tick,
    so persistence origin lookups must bucket, never match exact timestamps."""
    ts = _utc_time(value)
    return datetime.fromtimestamp(round(ts.timestamp() / 60.0) * 60.0, tz=UTC)


class ForecastSkillMetric(BaseModel):
    n: int
    mae: float | None
    bias: float | None
    persistence_mae: float | None
    skill_vs_persistence: float | None


class ForecastSkillResponse(BaseModel):
    """Accuracy over settled durable outcomes.

    The persistence comparator is the absolute change between a scored row's
    realized target value and the realized value at its origin timestamp.  The
    latter is recovered from another settled outcome row whose ``target_ts``
    equals that origin.  Rows without that matching durable observation still
    count toward forecast MAE/bias, but are omitted from persistence MAE.
    """

    as_of: datetime
    persistence_baseline: str
    windows: dict[str, dict[str, dict[str, ForecastSkillMetric]]]


@router.get("/latest")
def latest_forecast(service: ForecastServiceDep) -> dict[str, Any]:
    payload = service.latest.to_dict()
    # Pre-warm-up bundles are zero-valued placeholders; consumers (UI, external
    # participant bots) need a machine-readable signal to not trade on them.
    payload["warm"] = service.is_warm
    return payload


@router.get("/history")
async def forecast_history(
    service: ForecastServiceDep,
    limit: int = Query(720, ge=1),
    target: str | None = None,
) -> list[dict[str, Any]]:
    try:
        records = service.history(limit=min(limit, HISTORY_MAXLEN), target=target)
        if records:
            return records
        if get_settings().forecast_history_reset_on_boot:
            # Chart history is session-scoped by config (a restart starts a clean
            # hub chart); durable outcome rows still feed /forecasts/skill.
            return []
        # A restart begins with an empty deque. Durable outcome rows preserve
        # the same origin-time response shape for the forecast UI/API.
        from eflux.forecasting.outcomes import history_from_outcomes

        return await history_from_outcomes(limit=min(limit, HISTORY_MAXLEN), target=target)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/skill", response_model=ForecastSkillResponse)
async def forecast_skill(session: DbSession) -> ForecastSkillResponse:
    """Return price-model accuracy from durable settled forecast outcomes only."""
    now = datetime.now(UTC)
    # 12h of margin past the 7d window so persistence origins for rows at the
    # window edge still resolve to a settled observation.
    oldest = now - timedelta(days=7, hours=12)
    rows = list(
        (
            await session.execute(
                # No horizon filter here: every settled row (any horizon) is an
                # observation for the persistence baseline; the metric loops
                # below filter to SKILL_HORIZONS themselves.
                select(ForecastOutcome).where(
                    ForecastOutcome.market.in_(SKILL_TARGETS),
                    ForecastOutcome.realized.is_not(None),
                    ForecastOutcome.target_ts >= oldest,
                )
            )
        ).scalars()
    )

    # ForecastOutcome has no price anchor column.  Use the already-fetched
    # settled rows as the durable observation series for the origin-time
    # persistence value, keyed by MINUTE — refresh ticks drift sub-second per
    # cycle, so exact-timestamp matching silently excluded every row and the
    # persistence baseline read null everywhere (2026-07-11).
    origin_values: dict[tuple[str, datetime], float] = {}
    for row in rows:
        origin_values.setdefault((row.market, _minute_key(row.target_ts)), row.realized)

    windows: dict[str, dict[str, dict[str, ForecastSkillMetric]]] = {}
    for label, delta in (("24h", timedelta(hours=24)), ("7d", timedelta(days=7))):
        cutoff = now - delta
        by_target: dict[str, dict[str, ForecastSkillMetric]] = {}
        for target in SKILL_TARGETS:
            by_horizon: dict[str, ForecastSkillMetric] = {}
            for horizon in SKILL_HORIZONS:
                scored = [
                    row
                    for row in rows
                    if row.market == target
                    and row.horizon == horizon
                    and _utc_time(row.target_ts) >= cutoff
                ]
                errors = [row.predicted - row.realized for row in scored]
                persistence_errors = [
                    abs(row.realized - origin)
                    for row in scored
                    if (origin := origin_values.get((target, _minute_key(row.origin_ts)))) is not None
                ]
                n = len(scored)
                mae = sum(abs(error) for error in errors) / n if n else None
                bias = sum(errors) / n if n else None
                persistence_mae = (
                    sum(persistence_errors) / len(persistence_errors) if persistence_errors else None
                )
                skill = (
                    1 - mae / persistence_mae
                    if n >= 10 and mae is not None and persistence_mae not in (None, 0)
                    else None
                )
                by_horizon[horizon] = ForecastSkillMetric(
                    n=n,
                    mae=mae,
                    bias=bias,
                    persistence_mae=persistence_mae,
                    skill_vs_persistence=skill,
                )
            by_target[target] = by_horizon
        windows[label] = by_target

    return ForecastSkillResponse(
        as_of=now,
        persistence_baseline=(
            "Durable realized(target_ts) minus the realized value recovered at origin_ts; "
            "rows without a stored origin observation are excluded from persistence MAE."
        ),
        windows=windows,
    )
