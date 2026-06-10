"""Unit tests for simulator setup."""

from __future__ import annotations

from decimal import Decimal

from eflux.agents.base import MarketSnapshot, OrderIntent
from eflux.agents.truthful import TruthfulAgent
from eflux.bridge.bus import InMemoryBus
from eflux.agents.zi import ZIAgent
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
    sim.add_builtin_vpp("stub-vpp", VPPParams(), ZIAgent())

    sim.refresh_data_sources()
    status = sim.data_source_status()

    assert status["summary"] == "Synthetic profiles"
    assert status["checked_at"] is not None
    assert status["sources"][0]["component"] == "stub-vpp PV"
    assert status["sources"][0]["status"] == "synthetic"


def test_data_source_reports_real_when_weather_covers_sim_hour():
    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp("solar", VPPParams(), ZIAgent())

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
    assert status["summary"] == "Open-Meteo + pvlib"


def test_data_source_status_recheck_after_ttl():
    from datetime import UTC, datetime, timedelta

    sim = Simulator(bus=InMemoryBus())
    sim.add_builtin_vpp("stub", VPPParams(), ZIAgent())
    sim.refresh_data_sources()
    # Age the check past the TTL; the next status read must re-check.
    stale = datetime.now(UTC) - timedelta(seconds=sim.DATA_SOURCE_TTL_SEC + 1)
    sim._data_source_status["checked_at"] = stale  # noqa: SLF001
    status = sim.data_source_status()
    assert status["checked_at"] > stale


def test_internal_trade_updates_both_vpp_performance():
    sim = Simulator(bus=InMemoryBus())
    seller = sim.add_builtin_vpp("seller", VPPParams(), ZIAgent())
    buyer = sim.add_builtin_vpp("buyer", VPPParams(), ZIAgent())
    sim_ts = sim.clock.now_sim()

    sim._submit_intent(seller, OrderIntent(side="sell", price=Decimal("40"), qty=Decimal("1")), sim_ts)  # noqa: SLF001
    sim._submit_intent(buyer, OrderIntent(side="buy", price=Decimal("50"), qty=Decimal("1")), sim_ts)  # noqa: SLF001

    assert seller.state.pnl == Decimal("40.0")
    assert buyer.state.pnl == Decimal("-40.0")
    assert seller.recent_trades[0]["side"] == "sell"
    assert buyer.recent_trades[0]["side"] == "buy"


def test_truthful_vpp_trades_within_seconds_via_accumulator():
    """Regression for the dimension bug: with a 1-second tick, per-tick net energy
    (~1e-3 kWh) never cleared min_qty, so Truthful (and the LLM-wrapped Truthful)
    agents never traded. The runner's pending_net_kwh accumulator fixes that —
    a deficit VPP must place a buy and fill within a realistic number of ticks."""
    sim = Simulator(bus=InMemoryBus())
    deficit = sim.add_builtin_vpp(
        "deficit-truthful",
        VPPParams(pv_kw_peak=0.0, load_kw_base=5.0),
        TruthfulAgent(),
    )
    counter = sim.add_builtin_vpp("counter-seller", VPPParams(), ZIAgent())
    sim_ts = sim.clock.now_sim()
    # Resting ask the truthful buy (at price_ref=50) can cross.
    sim._submit_intent(  # noqa: SLF001
        counter, OrderIntent(side="sell", price=Decimal("40"), qty=Decimal("5")), sim_ts
    )

    tick_h = 1.0 / 3600.0  # 1-second ticks, as in the live simulator
    market = MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot())
    for _ in range(60):
        sim._tick_vpp(deficit, sim_ts, tick_h, market)  # noqa: SLF001
        if deficit.recent_trades:
            break

    assert deficit.recent_trades, "deficit VPP should trade within 60 one-second ticks"
    assert deficit.recent_trades[0]["side"] == "buy"
    # The accumulator was debited by the quoted qty — it must not keep growing
    # unboundedly negative after the order went out.
    assert abs(deficit.state.pending_net_kwh) < 0.02
