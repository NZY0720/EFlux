"""Public contracts for Phase-A forecast skill and deployment presets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eflux.db.models import ForecastOutcome


@pytest.mark.asyncio
async def test_forecast_skill_empty_data_has_complete_shape(client):
    response = await client.get("/forecasts/skill")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload["windows"]) == {"24h", "7d"}
    for window in payload["windows"].values():
        assert set(window) == {"price_real", "price_p2p"}
        for target in window.values():
            assert set(target) == {"5m", "1h", "12h"}
            assert target["5m"] == {
                "n": 0,
                "mae": None,
                "bias": None,
                "persistence_mae": None,
                "skill_vs_persistence": None,
            }


@pytest.mark.asyncio
async def test_forecast_skill_uses_durable_origin_observations(client, db_session):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    for i in range(10):
        origin = now - timedelta(minutes=30 + i)
        # This auxiliary outcome is the stored observation at the scored
        # forecast's origin timestamp. It deliberately has an unscored horizon.
        db_session.add(
            ForecastOutcome(
                origin_ts=origin - timedelta(minutes=5),
                target_ts=origin,
                horizon="source",
                market="price_real",
                predicted=90 + i,
                realized=90 + i,
            )
        )
        db_session.add(
            ForecastOutcome(
                origin_ts=origin,
                target_ts=origin + timedelta(minutes=5),
                horizon="5m",
                market="price_real",
                predicted=102 + i,
                realized=100 + i,
            )
        )
    await db_session.commit()

    response = await client.get("/forecasts/skill")

    assert response.status_code == 200, response.text
    metric = response.json()["windows"]["24h"]["price_real"]["5m"]
    assert metric["n"] == 10
    assert metric["mae"] == pytest.approx(2.0)
    assert metric["bias"] == pytest.approx(2.0)
    assert metric["persistence_mae"] == pytest.approx(10.0)
    assert metric["skill_vs_persistence"] == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_vpp_presets_are_public_and_match_deploy_fields(client):
    response = await client.get("/vpps/presets")

    assert response.status_code == 200, response.text
    presets = response.json()
    assert set(presets) == {"Solar Trader", "Battery Arbitrageur", "Demand Optimizer"}
    assert set(presets["Solar Trader"]) == {
        "pv", "batt", "load", "wind", "loadProfile", "algorithm", "llm", "online", "beta"
    }
    assert presets["Battery Arbitrageur"]["batt"] == 20


@pytest.mark.asyncio
async def test_market_snapshot_includes_provenance_and_session(client):
    response = await client.get("/market/snapshot")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data_provenance"] in {"real", "cached", "synthetic"}
    assert set(payload["session"]) == {"market_mode", "sim_time", "wall_time"}


@pytest.mark.asyncio
async def test_forecast_skill_persistence_tolerates_refresh_drift(client, db_session):
    """Refresh ticks drift sub-second; origin matching must bucket by minute,
    not require exact timestamp equality (which nulled every baseline)."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)

    def realized_at(minutes_before_now: float) -> float:
        # One consistent realized series: any row settling minute m carries the
        # same value regardless of which forecast produced it.
        return 200.0 - minutes_before_now

    for i in range(10):
        origin_m = 30 + i
        target_m = origin_m - 5
        origin = now - timedelta(minutes=origin_m)
        db_session.add(
            ForecastOutcome(
                # Observation row lands 0.7s off the scored row's origin minute.
                origin_ts=origin - timedelta(minutes=5),
                target_ts=origin + timedelta(seconds=0.7),
                horizon="source",
                market="price_real",
                predicted=realized_at(origin_m),
                realized=realized_at(origin_m),
            )
        )
        db_session.add(
            ForecastOutcome(
                origin_ts=origin + timedelta(seconds=1.4),
                target_ts=now - timedelta(minutes=target_m),
                horizon="5m",
                market="price_real",
                predicted=realized_at(target_m) + 2.0,
                realized=realized_at(target_m),
            )
        )
    await db_session.commit()

    response = await client.get("/forecasts/skill")

    assert response.status_code == 200, response.text
    metric = response.json()["windows"]["24h"]["price_real"]["5m"]
    assert metric["n"] == 10
    assert metric["mae"] == pytest.approx(2.0)
    # Persistence baseline: |realized(target) - realized(origin)| = 5 per row.
    assert metric["persistence_mae"] == pytest.approx(5.0)
    assert metric["skill_vs_persistence"] == pytest.approx(1.0 - 2.0 / 5.0)
