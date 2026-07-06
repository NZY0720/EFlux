from __future__ import annotations

from decimal import Decimal

import pytest

from eflux.agents.base import BaseAgent, MarketSnapshot
from eflux.bridge.bus import InMemoryBus
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams


class NoopAgent(BaseAgent):
    def decide(self, ctx):
        return []


def _vpp(*, pv_kw: float, load_kw: float, battery_kwh: float = 10.0, battery_kw_max: float = 10.0):
    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp(
        "balance",
        VPPParams(
            pv_kw_peak=pv_kw,
            load_kw_base=load_kw,
            battery_kwh=battery_kwh,
            battery_kw_max=battery_kw_max,
            forecast_noise_std=0.0,
        ),
        NoopAgent(),
    )
    vpp.pv.output_kw = lambda sim_ts, rng: pv_kw
    vpp.load.draw_kw = lambda sim_ts, rng: load_kw
    return sim, vpp


def _tick(sim: Simulator, vpp, *, tick_h: float = 1.0) -> None:
    sim_ts = sim.clock.now_sim()
    market = MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot())
    sim._tick_vpp(vpp, sim_ts, tick_h, market)


def test_no_trade_generation_charges_soc_then_overflows_to_pending():
    sim, vpp = _vpp(pv_kw=4.0, load_kw=0.0, battery_kwh=5.0, battery_kw_max=3.0)
    vpp.battery.soc_kwh = 1.0
    vpp.state.soc_kwh = 1.0

    _tick(sim, vpp)

    assert vpp.battery.soc_kwh == pytest.approx(4.0)
    assert vpp.state.pending_net_kwh == pytest.approx(1.0)


def test_pure_surplus_fills_capacity_then_pending_accumulates_capped():
    sim, vpp = _vpp(pv_kw=8.0, load_kw=0.0, battery_kwh=5.0, battery_kw_max=10.0)
    vpp.battery.soc_kwh = 4.0
    vpp.state.soc_kwh = 4.0

    _tick(sim, vpp)
    _tick(sim, vpp)

    assert vpp.battery.soc_kwh == pytest.approx(5.0)
    assert vpp.state.pending_net_kwh == pytest.approx(5.0)


def test_selling_forced_overflow_clears_pending_before_soc_discharge():
    sim, vpp = _vpp(pv_kw=0.0, load_kw=0.0)
    vpp.battery.soc_kwh = 6.0
    vpp.state.soc_kwh = 6.0
    vpp.state.pending_net_kwh = 3.0

    sim._settle_cash_and_energy(vpp, side="sell", qty_f=2.0, cash=Decimal("20"))
    assert vpp.state.pending_net_kwh == pytest.approx(1.0)
    assert vpp.battery.soc_kwh == pytest.approx(6.0)

    sim._settle_cash_and_energy(vpp, side="sell", qty_f=4.0, cash=Decimal("40"))
    assert vpp.state.pending_net_kwh == pytest.approx(0.0)
    assert vpp.battery.soc_kwh == pytest.approx(3.0)


def test_deficit_uses_soc_then_pending_and_buy_covers_before_charging():
    sim, vpp = _vpp(pv_kw=0.0, load_kw=4.0, battery_kwh=5.0, battery_kw_max=10.0)
    vpp.battery.soc_kwh = 3.0
    vpp.state.soc_kwh = 3.0

    _tick(sim, vpp)
    assert vpp.battery.soc_kwh == pytest.approx(0.0)
    assert vpp.state.pending_net_kwh == pytest.approx(-1.0)

    _tick(sim, vpp)
    assert vpp.battery.soc_kwh == pytest.approx(0.0)
    assert vpp.state.pending_net_kwh == pytest.approx(-5.0)

    sim._settle_cash_and_energy(vpp, side="buy", qty_f=7.0, cash=Decimal("70"))
    assert vpp.state.pending_net_kwh == pytest.approx(0.0)
    assert vpp.battery.soc_kwh == pytest.approx(2.0)
