from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import select

from eflux.config import get_settings
from eflux.db.models import AuditEvent, ProveOutRun, User
from eflux.db.session import get_sessionmaker
from eflux.evaluation import proveout, worker
from eflux.evaluation.proveout import CAISO_TZ, PriceHour, perfect_foresight_usd


def _two_day_prices() -> list[PriceHour]:
    values = [*range(24), 5, *([10] * 20), 40, 50, 10]
    start = datetime(2026, 1, 1, tzinfo=CAISO_TZ)
    return [
        PriceHour(timestamp=(start + timedelta(hours=hour)).astimezone(UTC), price=float(value))
        for hour, value in enumerate(values)
    ]


def test_perfect_foresight_two_day_fixture_is_exact():
    endowment = {
        "battery": {
            "power_mw": 2.0,
            "energy_mwh": 4.0,
            "round_trip_efficiency": 1.0,
            "cycle_cost_per_mwh": 5.0,
        }
    }

    actual = perfect_foresight_usd(_two_day_prices(), endowment)

    # Day 1: 2*(23-0)-10 + 2*(22-1)-10 = 68.
    # Day 2: 2*(50-5)-10 + 2*(40-10)-10 = 130.
    assert actual == 198.0
    print(f"PF fixture expected=198.0 actual={actual}")


def test_replay_is_deterministic_for_identical_inputs():
    endowment = {
        "battery": {
            "power_mw": 1.0,
            "energy_mwh": 2.0,
            "round_trip_efficiency": 0.9,
            "cycle_cost_per_mwh": 1.5,
        },
        "solar_mw": 1.0,
        "cash_usd": 10000.0,
    }
    strategy = {"algorithm": "battery_arbitrageur"}

    first = proveout.replay_price_hours(
        _two_day_prices(),
        endowment,
        strategy,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )
    second = proveout.replay_price_hours(
        _two_day_prices(),
        endowment,
        strategy,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )

    assert first == second
    print(f"determinism pair: {first!r} == {second!r}")


def test_missing_price_window_is_fetched_validated_and_cached(tmp_path):
    class CompleteClient:
        calls = 0

        def fetch_lmp_history_sync(self, *, node, start, end):
            self.calls += 1
            return [
                SimpleNamespace(interval_start=ts.to_pydatetime(), price=Decimal("42"))
                for ts in pd.date_range(start, end, freq="h", inclusive="left")
            ]

    client = CompleteClient()
    fetched = proveout.ensure_cached_price_window(
        date(2026, 1, 3),
        date(2026, 1, 4),
        cache_dir=tmp_path,
        client=client,
        attempts=1,
        retry_delay_sec=0,
    )

    assert fetched is True
    assert client.calls == 1
    assert (
        len(
            proveout.load_cached_price_hours(date(2026, 1, 3), date(2026, 1, 4), cache_dir=tmp_path)
        )
        == 48
    )
    assert [path.name for path in tmp_path.glob("lmp_*.parquet")] == [
        "lmp_TH_SP15_GEN-APND_2026-01-03_2026-01-05.parquet"
    ]
    assert (
        proveout.ensure_cached_price_window(
            date(2026, 1, 3),
            date(2026, 1, 4),
            cache_dir=tmp_path,
            client=client,
            attempts=1,
            retry_delay_sec=0,
        )
        is False
    )
    assert client.calls == 1


def test_incomplete_download_is_never_accepted_as_proveout_data(tmp_path):
    class PartialClient:
        def fetch_lmp_history_sync(self, *, node, start, end):
            return [SimpleNamespace(interval_start=start, price=Decimal("42"))]

    with pytest.raises(proveout.ProveOutDataError, match="missing local dates"):
        proveout.ensure_cached_price_window(
            date(2026, 1, 5),
            date(2026, 1, 5),
            cache_dir=tmp_path,
            client=PartialClient(),
            attempts=1,
            retry_delay_sec=0,
        )


async def test_worker_executes_tiny_cached_window_end_to_end(db_session, tmp_path, monkeypatch):
    node = get_settings().external_market_node
    safe_node = node.replace("/", "_")
    start, end = proveout._utc_bounds(date(2026, 1, 1), date(2026, 1, 1))
    index = pd.date_range(start, end, freq="h", inclusive="left")
    frame = pd.DataFrame({"lmp": [float(hour % 12) for hour in range(len(index))]}, index=index)
    frame.to_parquet(tmp_path / f"lmp_{safe_node}_2026-01-01_2026-01-03.parquet")
    monkeypatch.setattr(proveout, "PROVEOUT_CACHE_DIR", tmp_path)

    user = User(email="proveout-worker@example.com")
    db_session.add(user)
    await db_session.flush()
    run = ProveOutRun(
        user_id=user.id,
        endowment={
            "battery": {
                "power_mw": 1.0,
                "energy_mwh": 2.0,
                "round_trip_efficiency": 0.9,
                "cycle_cost_per_mwh": 0.0,
            },
            "solar_mw": 0.1,
            "wind": {"power_mw": 0.1, "mean_speed_mps": 7.0},
            "load": {"base_mw": 0.05, "profile": "commercial", "flexibility": 0.2},
            "cash_usd": 10000.0,
        },
        window_start=date(2026, 1, 1),
        window_end=date(2026, 1, 1),
        strategy={"algorithm": "battery_arbitrageur"},
        status="queued",
    )
    db_session.add(run)
    await db_session.commit()

    await worker.run_worker(once=True)

    async with get_sessionmaker()() as session:
        completed = await session.get(ProveOutRun, run.id)
        audit = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "proveout.completed",
                    AuditEvent.entity_id == run.id,
                )
            )
        ).scalar_one()
    assert completed is not None
    assert completed.status == "done"
    assert completed.report is not None
    assert completed.report["days"] == 1
    assert completed.report["engine"] == "Simulator + TradingGatewayV1"
    assert completed.report["replay_verified"] is True
    assert completed.report["solar_generation_kwh"] > 0
    assert completed.report["wind_generation_kwh"] > 0
    assert completed.report["load_consumption_kwh"] > 0
    assert completed.manifest is not None
    assert completed.evidence is not None
    assert completed.evidence_sha256 is not None
    assert completed.finished_at is not None
    assert audit.actor_user_id == user.id
