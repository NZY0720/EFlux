from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from eflux.agents.base import AgentContext, BaseAgent
from eflux.agents.decision import AgentDecision, OrderRequest
from eflux.bridge.bus import InMemoryBus
from eflux.market.delivery import OrderPurpose
from eflux.market.products import delivery_interval_containing
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


def test_physics_step_integrates_base_resources_without_implicit_battery_dispatch():
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    vpp = sim.add_builtin_vpp("vpp", VPPParams(), FixedAgent("buy", Decimal("1"), Decimal("0.01")))
    interval = delivery_interval_containing(NOW)
    before = vpp.battery.soc_kwh
    sim._step_physics(vpp, NOW, interval)
    meter = sim.meters.get(vpp.vpp_id, interval.interval_id)
    assert meter is not None
    assert meter.integrated_duration_sec == pytest.approx(sim.clock.tick_sim_sec)
    assert vpp.battery.soc_kwh == before
