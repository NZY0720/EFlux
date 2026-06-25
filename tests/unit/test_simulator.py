"""Unit tests for simulator setup."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from eflux.agents.base import MarketSnapshot, OrderIntent
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.zi import ZIAgent
from eflux.bridge.bus import InMemoryBus
from eflux.config import get_settings
from eflux.data.electricity_market import synthetic_quote
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

    assert status["summary"] == "Synthetic profiles + Synthetic CAISO price"
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
    assert status["summary"] == "Open-Meteo + pvlib + Synthetic CAISO price"


def test_data_source_status_recheck_after_ttl():
    from datetime import UTC, datetime, timedelta

    sim = Simulator(bus=InMemoryBus())
    sim.add_builtin_vpp("stub", VPPParams(), ZIAgent())
    sim.refresh_data_sources()
    # Age the check past the TTL; the next status read must re-check.
    stale = datetime.now(UTC) - timedelta(seconds=sim.DATA_SOURCE_TTL_SEC + 1)
    sim._data_source_status["checked_at"] = stale
    status = sim.data_source_status()
    assert status["checked_at"] > stale


def test_battery_intents_do_not_debit_pending_balance():
    """Battery-band quotes settle through the battery, not the PV-load
    imbalance — submitting one must leave pending_net_kwh untouched."""
    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp("batt", VPPParams(), ZIAgent())
    vpp.state.pending_net_kwh = -0.5
    sim._submit_intent(
        vpp,
        OrderIntent(side="sell", price=Decimal("52"), qty=Decimal("0.05"), dispatched=True),
        sim.clock.now_sim(),
    )
    assert vpp.state.pending_net_kwh == -0.5


def test_internal_trade_updates_both_vpp_performance():
    sim = Simulator(bus=InMemoryBus())
    seller = sim.add_builtin_vpp("seller", VPPParams(), ZIAgent())
    buyer = sim.add_builtin_vpp("buyer", VPPParams(), ZIAgent())
    sim_ts = sim.clock.now_sim()

    sim._submit_intent(seller, OrderIntent(side="sell", price=Decimal("40"), qty=Decimal("1")), sim_ts)
    sim._submit_intent(buyer, OrderIntent(side="buy", price=Decimal("50"), qty=Decimal("1")), sim_ts)

    assert seller.state.pnl == Decimal("40.0")
    assert buyer.state.pnl == Decimal("-40.0")
    assert seller.recent_trades[0]["side"] == "sell"
    assert buyer.recent_trades[0]["side"] == "buy"


@pytest.mark.asyncio
async def test_concurrent_external_submissions_are_serialized():
    """submit_external from many tasks at once must not corrupt the (sync)
    matching engine: unique order ids, and total resting qty == sum submitted
    (same side + price → nothing crosses)."""
    sim = Simulator(bus=InMemoryBus())
    n = 25

    results = await asyncio.gather(
        *(
            sim.submit_external(vpp_id=100 + i, side="buy", price=Decimal("10"), qty=Decimal("0.5"))
            for i in range(n)
        )
    )

    order_ids = [r["order_id"] for r in results]
    assert len(set(order_ids)) == n
    assert all(r["remaining_qty"] == "0.5" for r in results)
    bid = sim.engine.book.best_bid()
    assert bid is not None
    assert bid.total_qty == Decimal("12.5")
    assert len(bid.orders) == n


@pytest.mark.asyncio
async def test_submit_external_routes_remainder_to_caiso_when_book_empty():
    """An SDK order that crosses the CAISO import price with no P2P liquidity
    settles fully against the external market: the response carries the external
    fill, the order does not rest, and the event reaches the tape. The external
    leg is publish-only on this path (no in-memory VPP), so it lands in trade_log
    without mutating SimulatorVPP state."""
    sim = Simulator(bus=InMemoryBus())
    sim._external_market_quote = synthetic_quote(
        price=Decimal("40"),
        status="real",
        source="CAISO OASIS RTM",
        transaction_fee=Decimal("2"),
    )

    result = await sim.submit_external(
        vpp_id=777, side="buy", price=Decimal("60"), qty=Decimal("1.0")
    )

    assert result["remaining_qty"] == "0"
    ext = [t for t in result["trades"] if t["kind"] == "external.trade"]
    assert len(ext) == 1
    assert float(ext[0]["price"]) == 42.0  # import_price = raw_lmp 40 + 2 fee
    assert float(ext[0]["raw_lmp"]) == 40.0
    assert sim.engine.book.best_bid() is None  # crossed order did not rest
    assert any(e.kind == "external.trade" for e in sim.trade_log)


@pytest.mark.asyncio
async def test_submit_external_rests_when_quote_not_live():
    """A synthetic/disabled quote disables external routing, so the same order
    rests on the book instead of settling against CAISO."""
    sim = Simulator(bus=InMemoryBus())
    # default quote is synthetic → external_trading_enabled is False
    result = await sim.submit_external(
        vpp_id=778, side="buy", price=Decimal("60"), qty=Decimal("1.0")
    )

    assert result["remaining_qty"] == "1.0"
    assert not any(t["kind"] == "external.trade" for t in result["trades"])
    assert sim.engine.book.best_bid() is not None


def test_expired_order_refunds_pending_balance():
    """An expired (TTL'd) order must hand its unfilled remainder back to the
    accumulator — the submit-time debit 'spoke for' energy that was never
    delivered, and without the refund the agent understates its position."""
    from datetime import timedelta

    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp("seller", VPPParams(), ZIAgent())
    vpp.state.pending_net_kwh = 2.0
    sim_ts = sim.clock.now_sim()
    sim._submit_intent(
        vpp, OrderIntent(side="sell", price=Decimal("60"), qty=Decimal("2")), sim_ts
    )
    assert vpp.state.pending_net_kwh == 0.0
    assert len(vpp.open_order_ids) == 1

    sim._expire_orders(sim_ts + timedelta(seconds=sim.order_ttl_sec + 1))

    assert vpp.state.pending_net_kwh == 2.0
    assert vpp.open_order_ids == []


def test_expired_dispatched_order_does_not_refund():
    """Battery-band quotes never debit the accumulator, so their expiry must
    not credit it either."""
    from datetime import timedelta

    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp("batt", VPPParams(), ZIAgent())
    vpp.state.pending_net_kwh = -0.5
    sim_ts = sim.clock.now_sim()
    sim._submit_intent(
        vpp,
        OrderIntent(side="sell", price=Decimal("52"), qty=Decimal("0.05"), dispatched=True),
        sim_ts,
    )

    sim._expire_orders(sim_ts + timedelta(seconds=sim.order_ttl_sec + 1))

    assert vpp.state.pending_net_kwh == -0.5


def test_open_orders_net_by_vpp_signs_and_dispatched_excluded():
    """Resting book exposure per VPP follows the pending convention (sell +,
    buy -) and skips dispatched (battery-band/gas) quotes — this is what lets
    demand_beta see the deficit already parked in the book."""
    sim = Simulator(bus=InMemoryBus())
    buyer = sim.add_builtin_vpp("buyer", VPPParams(), ZIAgent())
    seller = sim.add_builtin_vpp("seller", VPPParams(), ZIAgent())
    sim_ts = sim.clock.now_sim()
    # Non-crossing prices so everything rests.
    sim._submit_intent(
        buyer, OrderIntent(side="buy", price=Decimal("40"), qty=Decimal("3")), sim_ts
    )
    sim._submit_intent(
        seller, OrderIntent(side="sell", price=Decimal("60"), qty=Decimal("2")), sim_ts
    )
    sim._submit_intent(
        seller,
        OrderIntent(side="sell", price=Decimal("55"), qty=Decimal("1"), dispatched=True),
        sim_ts,
    )

    net = sim._open_orders_net_by_vpp()

    assert net[buyer.vpp_id] == pytest.approx(-3.0)
    assert net[seller.vpp_id] == pytest.approx(2.0)  # dispatched ask excluded


def test_market_balance_reports_aggregates():
    sim = Simulator(bus=InMemoryBus())
    sim.add_builtin_vpp("load", VPPParams(pv_kw_peak=0.0, load_kw_base=5.0), ZIAgent())
    sim.add_builtin_vpp("gas", VPPParams(gas_kw_max=20.0, load_kw_base=0.0), ZIAgent())
    sim_ts = sim.clock.now_sim()
    tick_h = 1.0 / 3600.0
    market = MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot())
    for vpp in sim.vpps.values():
        sim._tick_vpp(vpp, sim_ts, tick_h, market)

    balance = sim.market_balance()

    assert balance["load_kw"] > 0
    assert balance["gas_capacity_kw"] == 20.0
    assert balance["supply_demand_ratio"] is not None
    assert balance["net_kw"] == pytest.approx(
        balance["renewable_kw"] - balance["load_kw"], abs=2e-3
    )


def test_realprice_market_settles_full_qty_against_grid_no_p2p_matching():
    """Real-price market: every built-in order is a pure price-taker. A crossing
    order settles in full against the grid at import/export, and P2P matching never
    happens — even a resting peer order that would cross in a P2P market is ignored."""
    sim = Simulator(bus=InMemoryBus())
    sim.market_mode = "realprice"
    seller = sim.add_builtin_vpp("seller", VPPParams(), ZIAgent())
    buyer = sim.add_builtin_vpp("buyer", VPPParams(), ZIAgent())
    sim._external_market_quote = synthetic_quote(price=Decimal("40"), status="real", source="CAISO OASIS RTM")
    sim_ts = sim.clock.now_sim()
    sim._current_tick_h = 1.0

    # A resting peer bid that WOULD cross the seller's ask in a P2P market.
    sim.engine.submit(
        vpp_id=buyer.vpp_id,
        side="buy",
        price=Decimal("45"),
        qty=Decimal("0.5"),
        sim_ts=sim_ts,
        wall_ts=sim_ts,
    )
    seller.state.pending_net_kwh = 1.0

    # Ask 35 crosses the grid export price (40), so the full 1.0 kWh sells to CAISO.
    sim._submit_intent(
        seller,
        OrderIntent(side="sell", price=Decimal("35"), qty=Decimal("1.0")),
        sim_ts,
    )

    # No P2P trade happened: the peer bid still rests and no ask was booked.
    assert sim.engine.book.best_bid() is not None
    assert sim.engine.book.best_bid().price == Decimal("45")
    assert sim.engine.snapshot()["asks"] == []
    # Full quantity settled against the grid at the export price (40, fee 0).
    assert seller.state.pending_net_kwh == pytest.approx(0.0)
    assert seller.state.pnl == Decimal("40.0")
    assert seller.state.cumulative_energy_sold_kwh == pytest.approx(1.0)
    assert any(e.kind == "external.trade" for e in sim.trade_log)


def test_p2p_market_never_settles_against_grid():
    """P2P market (default): CAISO is reference-only. An order that would cross the
    grid import/export price just rests in the book — no external trade is made."""
    sim = Simulator(bus=InMemoryBus())  # default market_mode == "p2p"
    seller = sim.add_builtin_vpp("seller", VPPParams(), ZIAgent())
    sim._external_market_quote = synthetic_quote(price=Decimal("40"), status="real", source="CAISO OASIS RTM")
    sim_ts = sim.clock.now_sim()
    seller.state.pending_net_kwh = 1.0

    # Ask 35 crosses the grid export price (40), but in p2p mode it must rest.
    sim._submit_intent(
        seller,
        OrderIntent(side="sell", price=Decimal("35"), qty=Decimal("1.0")),
        sim_ts,
    )

    assert sim.engine.book.best_ask() is not None
    assert sim.engine.book.best_ask().price == Decimal("35")
    assert not any(e.kind == "external.trade" for e in sim.trade_log)
    assert seller.state.cumulative_energy_sold_kwh == pytest.approx(0.0)


def test_vpp_trade_count_is_cumulative_not_recent_buffer_length():
    sim = Simulator(bus=InMemoryBus())
    vpp = sim.add_builtin_vpp("counter", VPPParams(), ZIAgent())

    for i in range(55):
        sim._push_recent_trade(vpp, {"trade_id": i})

    assert vpp.trade_count == 55
    assert len(vpp.recent_trades) == 50
    assert vpp.recent_trades[0]["trade_id"] == 54
    assert vpp.recent_trades[-1]["trade_id"] == 5


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
    sim._submit_intent(
        counter, OrderIntent(side="sell", price=Decimal("40"), qty=Decimal("5")), sim_ts
    )

    tick_h = 1.0 / 3600.0  # 1-second ticks, as in the live simulator
    market = MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot())
    for _ in range(60):
        sim._tick_vpp(deficit, sim_ts, tick_h, market)
        if deficit.recent_trades:
            break

    assert deficit.recent_trades, "deficit VPP should trade within 60 one-second ticks"
    assert deficit.recent_trades[0]["side"] == "buy"
    # The accumulator was debited by the quoted qty — it must not keep growing
    # unboundedly negative after the order went out.
    assert abs(deficit.state.pending_net_kwh) < 0.02
