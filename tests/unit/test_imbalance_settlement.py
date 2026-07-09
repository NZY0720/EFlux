from __future__ import annotations

from decimal import Decimal

import pytest

from eflux.agents.base import AgentContext, BaseAgent, MarketSnapshot, OrderIntent
from eflux.bridge.bus import InMemoryBus
from eflux.config import get_settings
from eflux.data.electricity_market import synthetic_quote
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams


class NoopAgent(BaseAgent):
    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        return []


def _market(sim: Simulator) -> MarketSnapshot:
    sim_ts = sim.clock.now_sim()
    return MarketSnapshot.from_engine(
        sim_ts,
        sim.engine.snapshot(),
        external_market=sim._external_market_quote,
        market_mode=sim.market_mode,
    )


def _fixed_balance(vpp, *, pv_kw: float, load_kw: float) -> None:
    vpp.pv.output_kw = lambda sim_ts, rng: pv_kw
    vpp.load.draw_kw = lambda sim_ts, rng: load_kw


def test_unserved_load_settlement_penalizes_overflow():
    sim = Simulator(bus=InMemoryBus())
    sim._external_market_quote = synthetic_quote(price=Decimal("50"), status="real")
    vpp = sim.add_builtin_vpp(
        "deficit",
        VPPParams(pv_kw_peak=0.0, load_kw_base=0.0, battery_kwh=1.0, battery_kw_max=10.0),
        NoopAgent(),
    )
    vpp.battery.soc_kwh = 0.0
    vpp.state.soc_kwh = 0.0
    vpp.state.pending_net_kwh = -1.0
    _fixed_balance(vpp, pv_kw=0.0, load_kw=2.0)

    sim._tick_vpp(vpp, sim.clock.now_sim(), 1.0, _market(sim))

    assert vpp.state.pending_net_kwh == pytest.approx(-1.0)
    assert vpp.state.pnl == Decimal("-200.0")
    totals = sim.imbalance_totals(vpp.vpp_id)
    assert totals["unserved_load_kwh"] == pytest.approx(2.0)
    assert totals["settlement_cash"] == pytest.approx(-200.0)


def test_spilled_generation_settlement_records_default_zero_curtailment():
    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp(
        "spill",
        VPPParams(pv_kw_peak=0.0, load_kw_base=0.0, battery_kwh=1.0, battery_kw_max=10.0),
        NoopAgent(),
    )
    vpp.battery.soc_kwh = 1.0
    vpp.state.soc_kwh = 1.0
    vpp.state.pending_net_kwh = 1.0
    _fixed_balance(vpp, pv_kw=2.0, load_kw=0.0)

    sim._tick_vpp(vpp, sim.clock.now_sim(), 1.0, _market(sim))

    assert vpp.state.pending_net_kwh == pytest.approx(1.0)
    assert vpp.state.pnl == Decimal("0.0")
    totals = sim.imbalance_totals(vpp.vpp_id)
    assert totals["spilled_generation_kwh"] == pytest.approx(2.0)
    assert totals["settlement_cash"] == pytest.approx(0.0)


def test_imbalance_settlement_disabled_preserves_clamp_without_pnl(monkeypatch):
    monkeypatch.setenv("EFLUX_IMBALANCE_SETTLEMENT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        sim = Simulator(bus=InMemoryBus())
        vpp = sim.add_builtin_vpp(
            "legacy-clamp",
            VPPParams(pv_kw_peak=0.0, load_kw_base=0.0, battery_kwh=1.0, battery_kw_max=10.0),
            NoopAgent(),
        )
        vpp.battery.soc_kwh = 0.0
        vpp.state.soc_kwh = 0.0
        vpp.state.pending_net_kwh = -1.0
        _fixed_balance(vpp, pv_kw=0.0, load_kw=2.0)

        sim._tick_vpp(vpp, sim.clock.now_sim(), 1.0, _market(sim))

        assert vpp.state.pending_net_kwh == pytest.approx(-1.0)
        assert vpp.state.pnl == Decimal("0")
        assert sim.imbalance_totals(vpp.vpp_id)["unserved_load_kwh"] == pytest.approx(0.0)
    finally:
        get_settings.cache_clear()


def test_realprice_backstop_sell_clears_full_pending_surplus():
    sim = Simulator(bus=InMemoryBus())
    sim.market_mode = "realprice"
    sim._external_market_quote = synthetic_quote(price=Decimal("40"), status="real")
    vpp = sim.add_builtin_vpp(
        "full-seller",
        VPPParams(pv_kw_peak=0.0, load_kw_base=0.0, battery_kwh=10.0, battery_kw_max=10.0),
        NoopAgent(),
    )
    vpp.battery.soc_kwh = 10.0
    vpp.state.soc_kwh = 10.0
    _fixed_balance(vpp, pv_kw=1.0, load_kw=0.0)

    sim._tick_vpp(vpp, sim.clock.now_sim(), 1.0, _market(sim))

    assert vpp.state.pending_net_kwh == pytest.approx(0.0)
    assert vpp.state.pnl == Decimal("40.0")
    assert vpp.state.cumulative_energy_sold_kwh == pytest.approx(1.0)
    assert [t.side for t in sim.trade_log] == ["sell"]


def test_realprice_backstop_buy_clears_full_pending_deficit():
    sim = Simulator(bus=InMemoryBus())
    sim.market_mode = "realprice"
    sim._external_market_quote = synthetic_quote(price=Decimal("40"), status="real")
    vpp = sim.add_builtin_vpp(
        "empty-buyer",
        VPPParams(pv_kw_peak=0.0, load_kw_base=0.0, battery_kwh=10.0, battery_kw_max=10.0),
        NoopAgent(),
    )
    vpp.battery.soc_kwh = 0.0
    vpp.state.soc_kwh = 0.0
    _fixed_balance(vpp, pv_kw=0.0, load_kw=1.0)

    sim._tick_vpp(vpp, sim.clock.now_sim(), 1.0, _market(sim))

    assert vpp.state.pending_net_kwh == pytest.approx(0.0)
    assert vpp.state.pnl == Decimal("-40.0")
    assert vpp.state.cumulative_energy_bought_kwh == pytest.approx(1.0)
    assert [t.side for t in sim.trade_log] == ["buy"]


def test_p2p_mode_does_not_submit_physical_backstop():
    sim = Simulator(bus=InMemoryBus())
    sim._external_market_quote = synthetic_quote(price=Decimal("40"), status="real")
    vpp = sim.add_builtin_vpp(
        "p2p-full",
        VPPParams(pv_kw_peak=0.0, load_kw_base=0.0, battery_kwh=10.0, battery_kw_max=10.0),
        NoopAgent(),
    )
    vpp.battery.soc_kwh = 10.0
    vpp.state.soc_kwh = 10.0
    _fixed_balance(vpp, pv_kw=1.0, load_kw=0.0)

    sim._tick_vpp(vpp, sim.clock.now_sim(), 1.0, _market(sim))

    assert vpp.state.pending_net_kwh == pytest.approx(1.0)
    assert vpp.state.pnl == Decimal("0")
    assert len(sim.trade_log) == 0
