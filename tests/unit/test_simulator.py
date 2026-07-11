"""Simulator setup and aggregate-state tests not covered by V2 runtime suites."""

from __future__ import annotations

from eflux.agents.gas import GasGeneratorAgent
from eflux.agents.truthful import TruthfulAgent
from eflux.bridge.bus import InMemoryBus
from eflux.config import get_settings
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams


def test_default_sim_epoch_uses_site_timezone(monkeypatch):
    monkeypatch.setenv("EFLUX_SITE_TIMEZONE", "Asia/Shanghai")
    get_settings.cache_clear()
    sim = Simulator(bus=InMemoryBus())
    assert getattr(sim.clock.clock.sim_epoch.tzinfo, "key", None) == "Asia/Shanghai"


def test_data_source_status_reports_startup_check_for_builtin_vpps():
    sim = Simulator(bus=InMemoryBus())
    sim.add_builtin_vpp("stub-vpp", VPPParams(), TruthfulAgent())
    sim.refresh_data_sources()
    status = sim.data_source_status()
    assert status["summary"] == "Synthetic profiles + Synthetic CAISO price"
    assert status["checked_at"] is not None
    assert status["sources"][0]["component"] == "stub-vpp PV"
    assert status["sources"][0]["status"] == "synthetic"


def test_data_source_reports_real_when_weather_covers_sim_hour():
    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp("solar", VPPParams(), TruthfulAgent())

    class FakeWeather:
        empty = False

        def __init__(self, index):
            self.index = index

    class FakeModel:
        def __init__(self, weather):
            self.weather = weather

    target = sim.clock.now_sim().replace(minute=0, second=0, microsecond=0)
    vpp.pv.physical_model = FakeModel(FakeWeather([target]))
    sim.refresh_data_sources()
    status = sim.data_source_status()
    assert status["sources"][0]["status"] == "real"
    assert status["summary"] == "Open-Meteo + pvlib + Synthetic CAISO price"


def test_data_source_status_rechecks_after_ttl():
    from datetime import UTC, datetime, timedelta

    sim = Simulator(bus=InMemoryBus())
    sim.add_builtin_vpp("stub", VPPParams(), TruthfulAgent())
    sim.refresh_data_sources()
    stale = datetime.now(UTC) - timedelta(seconds=sim.DATA_SOURCE_TTL_SEC + 1)
    sim._data_source_status["checked_at"] = stale
    assert sim.data_source_status()["checked_at"] > stale


def test_market_balance_reports_physical_aggregates():
    sim = Simulator(bus=InMemoryBus())
    load = sim.add_builtin_vpp("load", VPPParams(pv_kw_peak=0.0, load_kw_base=5.0), TruthfulAgent())
    gas = sim.add_builtin_vpp(
        "gas",
        VPPParams(
            gas_kw_max=20.0,
            battery_kwh=0.0,
            battery_kw_max=0.0,
            load_kw_base=0.0,
            pv_kw_peak=0.0,
        ),
        GasGeneratorAgent(),
    )
    load.state.load_kw = 5.0
    load.state.update_net()
    gas.state.update_net()
    balance = sim.market_balance()
    assert balance["load_kw"] == 5.0
    assert balance["gas_capacity_kw"] == 20.0
    assert balance["net_kw"] == -5.0


def test_vpp_trade_count_is_cumulative_not_recent_buffer_length():
    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp("counter", VPPParams(), TruthfulAgent())
    for i in range(55):
        sim._push_recent_trade(vpp, {"trade_id": i})
    assert vpp.trade_count == 55
    assert len(vpp.recent_trades) == 50
    assert vpp.recent_trades[0]["trade_id"] == 54
    assert vpp.recent_trades[-1]["trade_id"] == 5
