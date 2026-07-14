from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from eflux.db.models import ForecastOutcome
from eflux.forecasting.outcomes import create_outcomes, history_from_outcomes, settle_outcomes
from eflux.forecasting.service import ForecastService


@pytest.mark.asyncio
async def test_forecast_service_persists_and_settles_outcomes(db_session):
    created: list[dict] = []
    settled: list[dict] = []

    async def on_created(rows):
        created.extend(rows)
        await create_outcomes(rows)

    async def on_settled(rows):
        settled.extend(rows)
        await settle_outcomes(rows)

    service = ForecastService(on_outcomes_created=on_created, on_outcomes_settled=on_settled)
    start = datetime(2026, 6, 1, tzinfo=UTC)
    service.observe(start, price_real=50.0)
    service.refresh(start)
    await asyncio.sleep(0.05)

    service.observe(start + timedelta(hours=12), price_real=55.0)
    await asyncio.sleep(0.05)

    rows = list((await db_session.execute(select(ForecastOutcome))).scalars())
    twelve_hour = next(row for row in rows if row.market == "price_real" and row.horizon == "12h")
    assert len(created) == 15
    assert settled
    assert twelve_hour.realized == pytest.approx(55.0)

    # The API fallback shape is available after a cold in-memory deque/restart.
    history = await history_from_outcomes(limit=10, target="price_real")
    assert history[0]["forecasts"]["price_real"]["12h"] == pytest.approx(twelve_hour.predicted)
    assert history[0]["realized"]["price_real"] == pytest.approx(55.0)


def test_forecast_gap_leaves_overdue_targets_unresolved():
    settled: list[dict] = []
    service = ForecastService(on_outcomes_settled=settled.extend)
    start = datetime(2026, 6, 1, tzinfo=UTC)
    service.observe(start, price_real=50.0)
    service.refresh(start)

    service.observe(start + timedelta(hours=13), price_real=99.0)

    assert settled == []
    assert not any(
        row["market"] == "price_real" and row["origin_ts"] == start
        for row in service._pending_outcomes
    )
