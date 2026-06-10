"""Unit tests for simulator setup."""

from __future__ import annotations

from decimal import Decimal

from eflux.agents.base import OrderIntent
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
