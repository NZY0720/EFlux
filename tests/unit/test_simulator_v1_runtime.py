from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from eflux.agents.base import AgentContext, BaseAgent, ExternalControlAgent
from eflux.agents.decision import AgentDecision, OrderRequest
from eflux.bridge.bus import InMemoryBus
from eflux.market.delivery import OrderPurpose
from eflux.market.ledger import LedgerCategory
from eflux.market.products import delivery_horizon, delivery_interval_containing
from eflux.market.replay import replay_and_verify
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams

NOW = datetime(2026, 7, 11, 12, 3, tzinfo=UTC)


@dataclass
class FixedAgent(BaseAgent):
    side: str
    price: Decimal
    qty_kwh: Decimal

    def decide(self, ctx: AgentContext) -> AgentDecision:
        return AgentDecision(
            orders=(
                OrderRequest(
                    self.side,
                    self.price,
                    self.qty_kwh,
                    ctx.primary_interval,
                    OrderPurpose.BALANCE,
                    ttl_sec=ctx.decision_interval_sec,
                ),
            )
        )


def _sim_with_crossing_agents() -> tuple[Simulator, object, object]:
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    seller = sim.add_builtin_vpp(
        "seller",
        VPPParams(
            pv_kw_peak=6,
            battery_kwh=0,
            battery_kw_max=0,
            load_kw_base=0,
        ),
        FixedAgent("sell", Decimal("50"), Decimal("0.5")),
    )
    buyer = sim.add_builtin_vpp(
        "buyer",
        VPPParams(
            pv_kw_peak=0,
            battery_kwh=0,
            battery_kw_max=0,
            load_kw_base=6,
        ),
        FixedAgent("buy", Decimal("60"), Decimal("0.5")),
    )
    seller.state.pv_kw = 6.0
    seller.state.load_kw = 0.0
    seller.state.update_net()
    buyer.state.pv_kw = 0.0
    buyer.state.load_kw = 6.0
    buyer.state.update_net()
    return sim, seller, buyer


def test_runner_decision_round_uses_gateway_and_real_usd_without_touching_soc():
    sim, seller, buyer = _sim_with_crossing_agents()
    products = sim._ensure_products(NOW)
    seller_soc = seller.battery.soc_kwh
    buyer_soc = buyer.battery.soc_kwh
    sim._run_decision_round(NOW, products)
    assert sim.engine.trade_count == 1
    # Seeded arrival order places the 60 bid first in this cycle, so the resting
    # order sets the execution price under price-time priority.
    assert sim.gateway.ledger.balance(seller.vpp_id) == Decimal("0.030000")
    assert sim.gateway.ledger.balance(buyer.vpp_id) == Decimal("-0.030000")
    assert seller.state.pnl == Decimal("0.030000")
    assert buyer.state.pnl == Decimal("-0.030000")
    assert seller.battery.soc_kwh == seller_soc
    assert buyer.battery.soc_kwh == buyer_soc


def test_runner_meter_and_interval_settlement_reconcile_contracts():
    sim, seller, buyer = _sim_with_crossing_agents()
    product = sim._ensure_products(NOW)[0]
    sim._run_decision_round(NOW, (product,))
    sim.gateway.close_interval(product, sim_ts=product.start, wall_ts=product.start)
    sim.meters.integrate(
        participant_id=seller.vpp_id,
        interval=product,
        renewable_power_kw=6.0,
        uncontrolled_load_power_kw=0.0,
        duration_sec=product.duration_sec,
    )
    sim.meters.integrate(
        participant_id=buyer.vpp_id,
        interval=product,
        renewable_power_kw=0.0,
        uncontrolled_load_power_kw=6.0,
        duration_sec=product.duration_sec,
    )
    sim._settle_due_intervals(product.end)
    seller_position = sim.gateway.participants[seller.vpp_id].positions[product.interval_id]
    buyer_position = sim.gateway.participants[buyer.vpp_id].positions[product.interval_id]
    assert seller_position.imbalance_kwh == pytest.approx(0.0)
    assert buyer_position.imbalance_kwh == pytest.approx(0.0)
    assert sim.gateway.ledger.balance(seller.vpp_id) == Decimal("0.030000")
    assert sim.gateway.ledger.balance(buyer.vpp_id) == Decimal("-0.030000")
    report = replay_and_verify(list(sim._audit_buffer))
    assert report.ok, report.errors
    assert report.trade_count == 1
    # The first call also closes the currently-delivering bootstrap interval.
    assert report.delivery_count == 4


def test_run_interval_once_settles_consecutive_gap_free_products():
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    expected = delivery_horizon(NOW, count=3, market=sim.market_mode)
    sim_ts = NOW

    for _ in expected:
        sim_ts = sim.run_interval_once(sim_ts)

    settled = tuple(
        interval.interval_id
        for interval in sim.engine.intervals
        if interval.interval_id in sim._settled_intervals
        and interval.start >= expected[0].start
    )
    assert settled == tuple(interval.interval_id for interval in expected)
    assert all(left.end == right.start for left, right in pairwise(expected))


def test_physics_step_integrates_base_resources_without_implicit_battery_dispatch():
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    vpp = sim.add_builtin_vpp("vpp", VPPParams(), FixedAgent("buy", Decimal("1"), Decimal("0.01")))
    interval = delivery_interval_containing(NOW)
    before = vpp.battery.soc_kwh
    vpp.last_physics_sim_ts = NOW - timedelta(seconds=sim.clock.tick_sim_sec)
    sim._step_physics(vpp, NOW, interval)
    meter = sim.meters.get(vpp.vpp_id, interval.interval_id)
    assert meter is not None
    assert meter.integrated_duration_sec == pytest.approx(sim.clock.tick_sim_sec)
    assert vpp.battery.soc_kwh == before


def test_physics_step_uses_elapsed_sim_time_and_splits_interval_boundaries(monkeypatch):
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    vpp = sim.add_builtin_vpp(
        "metered",
        VPPParams(pv_kw_peak=0, battery_kwh=0, battery_kw_max=0, load_kw_base=0),
        ExternalControlAgent(),
    )
    boundary = NOW.replace(minute=5, second=0, microsecond=0)
    vpp.last_physics_sim_ts = boundary - timedelta(seconds=2)

    def fixed_state(vpp, sim_ts, interval):
        vpp.state.sim_ts = sim_ts
        vpp.state.pv_kw = 6.0
        vpp.state.wind_kw = 0.0
        vpp.state.load_kw = 3.0
        vpp.state.update_net()

    monkeypatch.setattr(sim, "_refresh_der_state", fixed_state)
    sim_ts = boundary + timedelta(seconds=8)
    sim._step_physics(vpp, sim_ts, delivery_interval_containing(sim_ts))

    prior = sim.meters.get(
        vpp.vpp_id, delivery_interval_containing(boundary - timedelta(seconds=1)).interval_id
    )
    current = sim.meters.get(vpp.vpp_id, delivery_interval_containing(boundary).interval_id)
    assert prior is not None
    assert current is not None
    assert prior.integrated_duration_sec == pytest.approx(2.0)
    assert current.integrated_duration_sec == pytest.approx(8.0)
    assert prior.renewable_generation_kwh == pytest.approx(6.0 * 2.0 / 3600.0)
    assert current.uncontrolled_load_kwh == pytest.approx(3.0 * 8.0 / 3600.0)


def test_pending_removal_retains_filled_position_until_fuel_settlement():
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    gas = sim.add_builtin_vpp(
        "gas",
        VPPParams(
            pv_kw_peak=0,
            battery_kwh=0,
            battery_kw_max=0,
            load_kw_base=0,
            gas_kw_max=12,
            gas_cost_per_mwh=60,
        ),
        ExternalControlAgent(),
    )
    buyer = sim.add_builtin_vpp(
        "buyer",
        VPPParams(pv_kw_peak=0, battery_kwh=0, battery_kw_max=0, load_kw_base=12),
        ExternalControlAgent(),
    )
    product, later_product = sim._ensure_products(NOW)[:2]
    sim.gateway.set_balance_projection(buyer.vpp_id, product, -1.0)
    sim.gateway.execute_decision(
        participant_id=gas.vpp_id,
        decision=AgentDecision(
            orders=(
                OrderRequest(
                    "sell",
                    Decimal("50"),
                    Decimal("1"),
                    product,
                    OrderPurpose.DISPATCHABLE,
                ),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    fill = sim.gateway.execute_decision(
        participant_id=buyer.vpp_id,
        decision=AgentDecision(
            orders=(
                OrderRequest("buy", Decimal("50"), Decimal("1"), product, OrderPurpose.BALANCE),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    assert len(fill.trades) == 1
    resting = sim.gateway.execute_decision(
        participant_id=gas.vpp_id,
        decision=AgentDecision(
            orders=(
                OrderRequest(
                    "sell",
                    Decimal("50"),
                    Decimal("0.5"),
                    later_product,
                    OrderPurpose.DISPATCHABLE,
                ),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    resting_order_id = resting.accepted_order_ids[0]
    assert sim.engine.get(resting_order_id) is not None

    assert sim.request_vpp_removal(gas.vpp_id)
    assert gas.vpp_id in sim.vpps
    assert gas.vpp_id in sim.gateway.participants
    assert sim.engine.get(resting_order_id) is None
    rejected = sim.gateway.execute_decision(
        participant_id=gas.vpp_id,
        decision=AgentDecision(
            orders=(
                OrderRequest(
                    "sell",
                    Decimal("50"),
                    Decimal("0.01"),
                    product,
                    OrderPurpose.DISPATCHABLE,
                ),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    assert "deactivated" in rejected.rejected[0].reason

    sim.gateway.close_interval(product, sim_ts=product.start, wall_ts=product.start)
    sim._settle_due_intervals(product.end)
    assert sim.gateway.ledger.breakdown(gas.vpp_id)[LedgerCategory.FUEL] == Decimal("-0.060000")
    assert gas.vpp_id not in sim.vpps
    assert gas.vpp_id not in sim.gateway.participants


def test_settlement_price_prefers_external_reference_over_other_product_trade():
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    products = sim._ensure_products(NOW)
    untraded, traded = products[:2]
    sim.engine.submit(
        interval=traded,
        vpp_id=1,
        side="sell",
        purpose=OrderPurpose.BALANCE,
        price=Decimal("99"),
        qty=Decimal("1"),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    sim.engine.submit(
        interval=traded,
        vpp_id=2,
        side="buy",
        purpose=OrderPurpose.BALANCE,
        price=Decimal("100"),
        qty=Decimal("1"),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    sim.imbalance_penalty_mult = 1.0

    prices = sim._settlement_prices(untraded)

    assert sim.engine.latest_price == Decimal("99")
    assert prices.long_imbalance_price == sim._external_market_quote.raw_lmp
    assert prices.short_imbalance_price == sim._external_market_quote.raw_lmp


def test_managed_replacement_keeps_participant_id_and_ledger_continuity():
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    original = sim.add_builtin_vpp(
        "managed",
        VPPParams(),
        ExternalControlAgent(),
        is_my_vpp=True,
        owner_id=7,
    )
    original.managed_def_id = 42
    sim.gateway.ledger.post(
        participant_id=original.vpp_id,
        category=LedgerCategory.INITIAL_INVENTORY,
        amount_usd=Decimal("12.34"),
        occurred_at=NOW,
    )
    replacement = sim.add_builtin_vpp(
        "managed",
        VPPParams(load_kw_base=2),
        ExternalControlAgent(),
        is_my_vpp=True,
        owner_id=7,
    )
    replacement.managed_def_id = 42
    temporary_id = replacement.vpp_id

    installed = sim.replace_managed_vpp(42, replacement)

    assert installed.vpp_id == original.vpp_id
    assert temporary_id not in sim.vpps
    assert temporary_id not in sim.gateway.participants
    assert sim.gateway.ledger.balance(installed.vpp_id) == Decimal("12.340000")
    assert installed.state.pnl == Decimal("12.340000")


def test_disabled_imbalance_settlement_keeps_position_without_cash_entries():
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    vpp = sim.add_builtin_vpp(
        "load",
        VPPParams(pv_kw_peak=0, battery_kwh=0, battery_kw_max=0, load_kw_base=12),
        ExternalControlAgent(),
    )
    product = sim._ensure_products(NOW)[0]
    sim.imbalance_settlement_enabled = False
    sim.meters.integrate(
        participant_id=vpp.vpp_id,
        interval=product,
        renewable_power_kw=0.0,
        uncontrolled_load_power_kw=12.0,
        duration_sec=product.duration_sec,
    )
    sim.gateway.close_interval(product, sim_ts=product.start, wall_ts=product.start)

    sim._settle_due_intervals(product.end)

    position = sim.gateway.participants[vpp.vpp_id].positions[product.interval_id]
    assert position.load_demand_kwh == pytest.approx(1.0)
    assert position.imbalance_kwh == pytest.approx(-1.0)
    assert not any(
        entry.category == LedgerCategory.IMBALANCE for entry in sim.gateway.ledger.entries
    )
    assert sim.imbalance_totals(vpp.vpp_id)["settlement_cash"] == 0.0
