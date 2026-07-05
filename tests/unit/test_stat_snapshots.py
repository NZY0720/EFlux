"""Unit tests for the simulator's stat-snapshot collection (durable leaderboard rows)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.truthful import TruthfulAgent
from eflux.bridge.bus import InMemoryBus
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams


def _sim_with_two_vpps() -> Simulator:
    sim = Simulator(bus=InMemoryBus())
    sim.add_builtin_vpp(
        "solar-a", VPPParams(pv_kw_peak=4.0, battery_kwh=10.0, battery_kw_max=3.0), TruthfulAgent()
    )
    sim.add_builtin_vpp("gas-a", VPPParams(gas_kw_max=20.0, gas_cost_per_kwh=60.0), TruthfulAgent())
    return sim


def test_collect_stat_rows_shape_and_identity():
    sim = _sim_with_two_vpps()
    sim.session_id = 7
    now = datetime.now(UTC)

    rows = sim._collect_stat_rows(42, now)

    assert len(rows) == 2
    by_name = {r["name"]: r for r in rows}
    solar = by_name["solar-a"]
    assert solar["session_id"] == 7
    assert solar["tick_no"] == 42
    assert solar["sim_ts"] == now
    assert solar["managed_def_id"] is None  # builtin identity is the name
    assert solar["category"] == "solar"
    assert solar["is_llm"] is False
    assert solar["llm_model"] is None
    assert isinstance(solar["pnl_usd"], Decimal)
    assert solar["pv_kw_peak"] == 4.0
    assert solar["battery_kw_max"] == 3.0
    assert by_name["gas-a"]["category"] == "gas"
    assert by_name["gas-a"]["gas_kw_max"] == 20.0


def test_collect_rows_track_live_state():
    sim = _sim_with_two_vpps()
    sim.session_id = 1
    vpp = next(v for v in sim.vpps.values() if v.name == "solar-a")
    vpp.state.pnl = Decimal("12345")  # internal $/MWh x kWh units
    vpp.trade_count = 9
    vpp.state.cumulative_energy_sold_kwh = 3.5

    row = next(
        r for r in sim._collect_stat_rows(1, datetime.now(UTC)) if r["name"] == "solar-a"
    )

    assert row["pnl_usd"] == Decimal("12.345")  # /1000 internal -> USD
    assert row["trade_count"] == 9
    assert row["energy_sold_kwh"] == 3.5


def test_snapshot_writer_noops_without_session():
    """session_id=None (tests, backtests, DB trouble) must keep the writer fully off."""
    sim = _sim_with_two_vpps()
    assert sim.session_id is None
    sim._maybe_snapshot_stats(1, datetime.now(UTC))
    assert sim._stats_task is None
