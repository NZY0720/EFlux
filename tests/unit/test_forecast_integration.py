from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.truthful import TruthfulAgent
from eflux.api.routers import forecasts
from eflux.bridge.bus import InMemoryBus
from eflux.config import get_settings
from eflux.forecasting.schema import ForecastBundle
from eflux.forecasting.service import ForecastService
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams


def _context_kwargs(sim: Simulator, vpp):
    sim_ts = sim.clock.now_sim()
    return {
        "vpp_id": vpp.vpp_id,
        "params": vpp.params,
        "state": vpp.state,
        "pv": vpp.pv,
        "battery": vpp.battery,
        "load": vpp.load,
        "market": MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot()),
        "rng": random.Random(1),
        "tick_duration_h": 1.0 / 3600.0,
    }


def test_agent_context_forecast_defaults_none_and_accepts_bundle():
    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp("ctx", VPPParams(), TruthfulAgent())
    kwargs = _context_kwargs(sim, vpp)

    assert AgentContext(**kwargs).forecast is None

    bundle = ForecastBundle.empty(as_of=datetime(2026, 1, 1, tzinfo=UTC))
    assert AgentContext(**kwargs, forecast=bundle).forecast is bundle


def test_context_forecast_hides_refreshed_never_warmed_service():
    sim = Simulator(bus=InMemoryBus())
    service = ForecastService()
    bundle = service.refresh(datetime(2026, 2, 1, 12, 0, tzinfo=UTC))
    sim.forecast_service = service

    assert bundle.model_version == "online-rls-v1"
    assert service.is_warm is False
    assert sim._context_forecast() is None


def test_save_forecast_state_skips_placeholder_and_persists_warmed_service(monkeypatch, tmp_path):
    monkeypatch.setenv("EFLUX_FORECAST_STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    sim = Simulator(bus=InMemoryBus())
    state_path = tmp_path / "state.json"

    sim.forecast_service = ForecastService()
    sim._save_forecast_state()

    assert not state_path.exists()

    start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    sim.forecast_service.warm_start(
        series={
            "price_real": [(start, 50.0)],
            "price_p2p": [(start, 49.0)],
        }
    )
    sim._save_forecast_state()

    assert state_path.exists()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_simulator_forecast_service_starts_and_refreshes(monkeypatch, tmp_path):
    monkeypatch.setenv("EFLUX_FORECAST_ENABLED", "true")
    monkeypatch.setenv("EFLUX_FORECAST_REFRESH_SEC", "3600")
    monkeypatch.setenv("EFLUX_FORECAST_STATE_DIR", str(tmp_path))
    get_settings.cache_clear()

    def fail_warmup(self, days: int):
        raise RuntimeError("offline test")

    monkeypatch.setattr(Simulator, "_load_forecast_warmup_data", fail_warmup)
    monkeypatch.setattr(Simulator, "_load_forecast_dam_prices", lambda self: None)
    sim = Simulator(bus=InMemoryBus())
    try:
        await sim.start()
        assert sim.forecast_service is not None

        ts = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
        bundle = sim.forecast_service.refresh(ts)

        assert sim.forecast_service.latest is bundle
        assert bundle.as_of == ts
    finally:
        await sim.stop()
        get_settings.cache_clear()


def test_latest_forecast_endpoint_returns_all_targets():
    service = ForecastService()
    service.refresh(datetime(2026, 3, 1, 0, 0, tzinfo=UTC))
    app = FastAPI()
    app.include_router(forecasts.router)
    app.state.forecast_service = service

    response = TestClient(app).get("/forecasts/latest")

    assert response.status_code == 200
    payload = response.json()
    for target in ("price_real", "price_p2p", "ghi", "temp_air", "wind_speed"):
        assert target in payload
        assert set(payload[target]) == {"5m", "1h", "12h"}


def test_forecast_history_endpoint_returns_records_with_optional_target_filter():
    service = ForecastService()
    ts = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    service.observe(ts, price_real=50.0, price_p2p=49.0)
    service.refresh(ts + timedelta(minutes=1))
    service.observe(ts + timedelta(minutes=2), price_real=52.0, price_p2p=51.0)
    service.refresh(ts + timedelta(minutes=3))
    app = FastAPI()
    app.include_router(forecasts.router)
    app.state.forecast_service = service

    response = TestClient(app).get("/forecasts/history?limit=1&target=price_real")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    record = payload[0]
    assert record["as_of"] == (ts + timedelta(minutes=3)).isoformat()
    assert set(record["forecasts"]) == {"price_real"}
    assert set(record["forecasts"]["price_real"]) == {"5m", "1h", "12h"}
    assert record["realized"] == {"price_real": 52.0}


@pytest.mark.asyncio
async def test_bootstrap_falls_back_to_cached_window_when_fresh_fetch_thin(monkeypatch, tmp_path):
    from datetime import date

    from eflux.agents.ppo.training_data import RealMarketData

    monkeypatch.setenv("EFLUX_FORECAST_STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    start = datetime(2026, 6, 1, tzinfo=UTC)

    def thin_warmup(self, days):
        # CAISO 429-throttled fetch: every chunk failed, zero price points.
        return RealMarketData(price={}, weather=None, wind=None, start=start, end=start)

    rich_price = {start + timedelta(hours=i): 45.0 + (i % 7) for i in range(200)}

    def cached_window(self, start_d, end_d):
        assert (start_d, end_d) == (date(2026, 6, 1), date(2026, 7, 1))
        return RealMarketData(price=rich_price, weather=None, wind=None, start=start, end=start + timedelta(hours=200))

    monkeypatch.setattr(Simulator, "_load_forecast_live_frames", lambda self: None)
    monkeypatch.setattr(Simulator, "_load_forecast_dam_prices", lambda self: None)
    monkeypatch.setattr(Simulator, "_load_forecast_warmup_data", thin_warmup)
    monkeypatch.setattr(Simulator, "_load_forecast_warmup_window", cached_window)
    monkeypatch.setattr(
        "eflux.agents.ppo.training_data.cached_price_windows",
        lambda *args, **kwargs: [(date(2026, 6, 1), date(2026, 7, 1))],
    )

    sim = Simulator(bus=InMemoryBus())
    try:
        await sim._bootstrap_forecast_service()

        assert sim.forecast_service is not None
        assert sim.forecast_service.is_warm
        assert sim.forecast_service.observation_count("price_real") == 200
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_bootstrap_rewarms_price_cold_restored_state(monkeypatch, tmp_path):
    from eflux.agents.ppo.training_data import RealMarketData

    monkeypatch.setenv("EFLUX_FORECAST_STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    ts = datetime(2026, 6, 1, tzinfo=UTC)

    weather_only = ForecastService()
    weather_only.observe(ts, ghi=500.0, temp_air=25.0, wind_speed=3.0)
    weather_only.save(tmp_path / "state.json")
    assert weather_only.observation_count() > 0
    assert not weather_only.is_warm

    fresh_price = {ts + timedelta(hours=i): 50.0 + (i % 5) for i in range(200)}

    def fresh_warmup(self, days):
        return RealMarketData(price=fresh_price, weather=None, wind=None, start=ts, end=ts + timedelta(hours=200))

    monkeypatch.setattr(Simulator, "_load_forecast_live_frames", lambda self: None)
    monkeypatch.setattr(Simulator, "_load_forecast_dam_prices", lambda self: None)
    monkeypatch.setattr(Simulator, "_load_forecast_warmup_data", fresh_warmup)

    sim = Simulator(bus=InMemoryBus())
    try:
        await sim._bootstrap_forecast_service()

        # Price-cold restored state is not adopted as-is: warm-start runs on top,
        # warming prices while keeping the restored weather observations.
        assert sim.forecast_service is not None
        assert sim.forecast_service.is_warm
        assert sim.forecast_service.observation_count("ghi") >= 1
    finally:
        get_settings.cache_clear()


def test_latest_forecast_endpoint_reports_warm_flag():
    service = ForecastService()
    service.refresh(datetime(2026, 3, 1, 0, 0, tzinfo=UTC))
    app = FastAPI()
    app.include_router(forecasts.router)
    app.state.forecast_service = service

    client = TestClient(app)
    assert client.get("/forecasts/latest").json()["warm"] is False

    start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    service.warm_start(series={"price_real": [(start, 50.0)], "price_p2p": [(start, 49.0)]})

    assert client.get("/forecasts/latest").json()["warm"] is True


def test_context_forecast_hides_stale_restored_bundle():
    sim = Simulator(bus=InMemoryBus())
    service = ForecastService()
    start = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    service.warm_start(series={"price_real": [(start, 50.0)], "price_p2p": [(start, 49.0)]})
    service.refresh(start)  # months older than the live clock ⇒ stale
    sim.forecast_service = service

    assert service.is_warm
    assert sim._context_forecast() is None
