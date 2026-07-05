"""Unit tests for the RiskGate plus a runner-level regression proving the default
limits reject nothing the built-in roster does today."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from eflux.agents.base import MarketSnapshot, OrderIntent
from eflux.agents.gas import GasGeneratorAgent
from eflux.agents.hybrid import RiskGate, RiskLimits
from eflux.agents.truthful import TruthfulAgent
from eflux.bridge.bus import InMemoryBus
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams
from eflux.vpp.der import Battery

GATE_LOGGER = "eflux.agents.hybrid.risk"


def _intent(side="sell", price="50", qty="1", dispatched=False):
    return OrderIntent(side=side, price=Decimal(price), qty=Decimal(qty), dispatched=dispatched)


def _battery(soc_kwh=5.0):
    return Battery(capacity_kwh=10.0, max_power_kw=3.0, eta_rt=0.9, soc_kwh=soc_kwh)


# --- static bands -----------------------------------------------------------

def test_in_band_order_accepted():
    d = RiskGate().validate([_intent(price="60", qty="2")], vpp_id=-1)
    assert len(d.accepted) == 1 and not d.rejected


def test_price_above_max_rejected():
    d = RiskGate().validate([_intent(price="2000")], vpp_id=-1)
    assert not d.accepted and "above max" in d.rejected[0].reason


def test_nonpositive_price_rejected():
    d = RiskGate(RiskLimits(price_min=Decimal("0"))).validate([_intent(price="0")], vpp_id=-1)
    assert "price <= 0" in d.rejected[0].reason


def test_dust_qty_rejected():
    d = RiskGate().validate([_intent(qty="0.001")], vpp_id=-1)
    assert "below min" in d.rejected[0].reason


def test_qty_above_max_rejected():
    d = RiskGate().validate([_intent(qty="5000")], vpp_id=-1)
    assert "above max" in d.rejected[0].reason


def test_notional_cap_when_set():
    gate = RiskGate(RiskLimits(max_notional=Decimal("100")))
    d = gate.validate([_intent(price="60", qty="2")], vpp_id=-1)  # notional 120 > 100
    assert "notional" in d.rejected[0].reason


# --- rate / count limits ----------------------------------------------------

def test_max_new_orders_per_tick():
    gate = RiskGate(RiskLimits(max_new_orders_per_tick=2))
    d = gate.validate([_intent(), _intent(), _intent()], vpp_id=-1)
    assert len(d.accepted) == 2 and len(d.rejected) == 1
    assert "new orders this tick" in d.rejected[0].reason


def test_max_open_orders_counts_existing():
    gate = RiskGate(RiskLimits(max_open_orders=5))
    d = gate.validate([_intent(), _intent()], vpp_id=-1, open_order_count=4)
    # 4 already resting + 1 accepted hits the cap of 5 → second rejected.
    assert len(d.accepted) == 1 and "open orders" in d.rejected[0].reason


# --- battery SOC feasibility ------------------------------------------------

def test_battery_discharge_within_soc_accepted():
    # deliverable = soc 5 * sqrt(0.9) ≈ 4.74 kWh
    d = RiskGate().validate([_intent("sell", "52", "4", dispatched=True)], vpp_id=-1, battery=_battery())
    assert len(d.accepted) == 1


def test_battery_discharge_over_soc_rejected():
    d = RiskGate().validate([_intent("sell", "52", "5", dispatched=True)], vpp_id=-1, battery=_battery())
    assert "exceeds deliverable" in d.rejected[0].reason


def test_battery_charge_over_room_rejected():
    # chargeable = room (10-5=5) / sqrt(0.9) ≈ 5.27 kWh
    d = RiskGate().validate([_intent("buy", "47", "6", dispatched=True)], vpp_id=-1, battery=_battery())
    assert "exceeds chargeable" in d.rejected[0].reason


def test_batch_consumes_battery_headroom():
    # two 3 kWh discharges: first ok (soc 5), draws 3/sqrt(0.9)=3.16 → soc 1.84,
    # deliverable now ~1.74 → second 3 kWh rejected.
    d = RiskGate().validate(
        [_intent("sell", "52", "3", dispatched=True), _intent("sell", "52", "3", dispatched=True)],
        vpp_id=-1,
        battery=_battery(),
    )
    assert len(d.accepted) == 1 and "exceeds deliverable" in d.rejected[0].reason


def test_gas_dispatched_order_skips_soc_check():
    # is_gas (gas_kw_max > 0) → dispatched sell settles through fuel, not storage.
    params = VPPParams(gas_kw_max=20.0, battery_kwh=0.0, battery_kw_max=0.0, load_kw_base=0.0)
    d = RiskGate().validate(
        [_intent("sell", "72", "8", dispatched=True)],  # 8 kWh >> battery deliverable
        vpp_id=-1,
        params=params,
        battery=_battery(soc_kwh=1.0),
    )
    assert len(d.accepted) == 1 and not d.rejected


def test_non_dispatched_order_not_soc_checked():
    # An ambient (renewable-balance) sell is not limited by SOC.
    d = RiskGate().validate([_intent("sell", "52", "9", dispatched=False)], vpp_id=-1, battery=_battery())
    assert len(d.accepted) == 1


# --- decision semantics -----------------------------------------------------

def test_requires_fallback_when_all_rejected():
    d = RiskGate().validate([_intent(price="2000")], vpp_id=-1)
    assert d.requires_fallback


def test_no_fallback_when_some_accepted():
    d = RiskGate().validate([_intent(price="50"), _intent(price="2000")], vpp_id=-1)
    assert not d.requires_fallback


def test_empty_batch_no_fallback():
    assert not RiskGate().validate([], vpp_id=-1).requires_fallback


# --- regression: the gate vetoes nothing the live roster does ---------------

def test_full_roster_tick_loop_has_zero_vetoes(caplog):
    """Drive a roster spanning the scenario's extremes (largest battery, costliest
    gas, biggest load, wind) through the real gate path for many ticks and assert
    the default limits reject nothing — the calibration guarantee for M2."""
    sim = Simulator(bus=InMemoryBus())
    sim.add_builtin_vpp(
        "big-truthful",
        VPPParams(pv_kw_peak=10.0, battery_kwh=30.0, battery_kw_max=8.0, load_kw_base=14.0, markup_floor=0.4),
        TruthfulAgent(price_ref=Decimal("53.0"), demand_beta=0.5),
    )
    sim.add_builtin_vpp(
        "deficit-truthful",
        VPPParams(pv_kw_peak=0.0, battery_kwh=20.0, load_kw_base=8.0),
        TruthfulAgent(price_ref=Decimal("47.0")),
    )
    sim.add_builtin_vpp("zi", VPPParams(pv_kw_peak=5.0, battery_kwh=10.0, load_kw_base=2.0), TruthfulAgent())
    sim.add_builtin_vpp(
        "wind-truthful",
        VPPParams(wind_kw_rated=15.0, battery_kwh=20.0, load_kw_base=4.0),
        TruthfulAgent(),
    )
    sim.add_builtin_vpp(
        "gas",
        VPPParams(gas_kw_max=20.0, gas_cost_per_kwh=72.0, battery_kwh=0.0, battery_kw_max=0.0, load_kw_base=0.0),
        GasGeneratorAgent(),
    )

    tick_h = 1.0 / 3600.0
    sim_ts = sim.clock.now_sim()
    with caplog.at_level(logging.WARNING, logger=GATE_LOGGER):
        for _ in range(150):
            sim._expire_orders(sim_ts)
            market = MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot(depth_levels=5))
            net = sim._open_orders_net_by_vpp()
            counts = sim._open_order_counts_by_vpp()
            for vpp in sim.vpps.values():
                sim._tick_vpp(
                    vpp, sim_ts, tick_h, market,
                    open_orders_net_kwh=net.get(vpp.vpp_id, 0.0),
                    open_order_count=counts.get(vpp.vpp_id, 0),
                )
            sim_ts = sim_ts + timedelta(seconds=1)

    vetoes = [r.getMessage() for r in caplog.records if "RiskGate vetoed" in r.getMessage()]
    assert not vetoes, f"gate should veto nothing the roster does, got: {vetoes[:5]}"
    # Sanity: the gate is in the path and the market is alive (orders rested/traded).
    assert sim.engine.book.best_bid() is not None or sim.engine.book.best_ask() is not None


def test_high_throughput_vpp_in_steady_state_not_vetoed(caplog):
    """Steady-state regression for the calibration review: a high-|net_kw| VPP whose
    orders rest unfilled requotes its balance every few ticks and accumulates resting
    orders until they TTL-expire. Run well past order_ttl_sec so the accumulation
    reaches steady state, and assert the open-order count climbs above the old cap of
    50 yet the gate vetoes nothing — the bug the 150-tick roster test could not catch."""
    sim = Simulator(bus=InMemoryBus())
    # Deficit VPP: load 14, no PV/wind → net ~-14 kW. It bids at price_ref=50, below
    # the gas merit order, with no counterparty here → every bid rests until TTL.
    vpp = sim.add_builtin_vpp(
        "lonely-deficit",
        VPPParams(pv_kw_peak=0.0, battery_kwh=20.0, load_kw_base=14.0, load_profile="flat"),
        TruthfulAgent(price_ref=Decimal("50.0")),
    )

    tick_h = 1.0 / 3600.0
    sim_ts = sim.clock.now_sim()
    peak = 0
    with caplog.at_level(logging.WARNING, logger=GATE_LOGGER):
        for _ in range(420):  # > 2 * order_ttl_sec (180s) → full steady state
            sim._expire_orders(sim_ts)
            market = MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot(depth_levels=5))
            counts = sim._open_order_counts_by_vpp()
            peak = max(peak, counts.get(vpp.vpp_id, 0))
            sim._tick_vpp(
                vpp, sim_ts, tick_h, market,
                open_orders_net_kwh=sim._open_orders_net_by_vpp().get(vpp.vpp_id, 0.0),
                open_order_count=counts.get(vpp.vpp_id, 0),
            )
            sim_ts = sim_ts + timedelta(seconds=1)

    vetoes = [r.getMessage() for r in caplog.records if "RiskGate vetoed" in r.getMessage()]
    assert not vetoes, f"steady-state high-throughput VPP must not be vetoed, got: {vetoes[:5]}"
    # The test must actually exercise the regime that broke the old default of 50.
    assert peak > 50, f"expected steady-state accumulation above the old cap, peaked at {peak}"
