"""Durable forecast-outcome storage and history reconstruction."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update

from eflux.config import get_settings
from eflux.db.models import ForecastOutcome
from eflux.db.session import get_sessionmaker
from eflux.forecasting.schema import HORIZONS

log = logging.getLogger(__name__)


async def create_outcomes(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        async with get_sessionmaker()() as session:
            for row in rows:
                exists = await session.scalar(
                    select(ForecastOutcome.id).where(
                        ForecastOutcome.origin_ts == row["origin_ts"],
                        ForecastOutcome.horizon == row["horizon"],
                        ForecastOutcome.market == row["market"],
                    )
                )
                if exists is None:
                    session.add(ForecastOutcome(**row))
            await _prune(session)
            await session.commit()
    except Exception:
        log.exception("Forecast outcome creation skipped")


async def settle_outcomes(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        async with get_sessionmaker()() as session:
            for row in rows:
                await session.execute(
                    update(ForecastOutcome)
                    .where(
                        ForecastOutcome.origin_ts == row["origin_ts"],
                        ForecastOutcome.horizon == row["horizon"],
                        ForecastOutcome.market == row["market"],
                        ForecastOutcome.realized.is_(None),
                    )
                    .values(realized=row["realized"])
                )
            await session.commit()
    except Exception:
        log.exception("Forecast outcome settlement skipped")


async def _prune(session) -> None:
    retention = max(0, get_settings().forecast_outcome_retention_days)
    if retention:
        cutoff = datetime.now(UTC) - timedelta(days=retention)
        await session.execute(delete(ForecastOutcome).where(ForecastOutcome.origin_ts < cutoff))


async def history_from_outcomes(limit: int, target: str | None) -> list[dict[str, Any]]:
    """Reconstruct API-shaped origin records from durable forecast rows."""
    if target is None:
        markets = None
    else:
        markets = {target}
    try:
        async with get_sessionmaker()() as session:
            query = (
                select(ForecastOutcome)
                .order_by(ForecastOutcome.origin_ts.desc())
                .limit(max(1, limit) * len(HORIZONS) * 5)
            )
            if markets is not None:
                query = query.where(ForecastOutcome.market.in_(markets))
            rows = list((await session.execute(query)).scalars())
    except Exception:
        log.exception("Durable forecast history unavailable")
        return []
    grouped: dict[datetime, dict[str, Any]] = {}
    for row in reversed(rows):
        record = grouped.setdefault(
            row.origin_ts,
            {"as_of": row.origin_ts.isoformat(), "forecasts": {}, "realized": {}},
        )
        record["forecasts"].setdefault(row.market, {})[row.horizon] = row.predicted
        if row.realized is not None:
            record["realized"][row.market] = row.realized
        else:
            record["realized"].setdefault(row.market, None)
    return list(grouped.values())[-max(0, limit) :]
