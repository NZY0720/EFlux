from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from eflux.agents.decision import AgentDecision, OrderRequest
from eflux.bridge.bus import InMemoryBus
from eflux.market.delivery import OrderPurpose
from eflux.market.gateway import TradingGatewayV1
from eflux.market.product_engine import ProductMatchingEngine
from eflux.market.products import DeliveryInterval
from eflux.simulator.runner import GRID_PARTICIPANT_ID, Simulator
from eflux.vpp.base import VPPParams

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _interval(market: str = "realprice") -> DeliveryInterval:
    return DeliveryInterval(
        market=market,
        start=NOW + timedelta(minutes=5),
        end=NOW + timedelta(minutes=10),
        gate_closure=NOW + timedelta(minutes=5),
        opens_at=NOW - timedelta(minutes=25),
    )


def _request(
    participant_id: int,
    side: str,
    price: str,
    qty: str,
    purpose: OrderPurpose,
    gateway: TradingGatewayV1,
    interval: DeliveryInterval,
):
    return gateway.execute_decision(
        participant_id=participant_id,
        decision=AgentDecision(
            orders=(
                OrderRequest(
                    side=side,
                    price=Decimal(price),
                    qty_kwh=Decimal(qty),
                    interval=interval,
                    purpose=purpose,
                ),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )


def test_realprice_product_blocks_peer_matching_but_allows_grid_counterparty():
    engine = ProductMatchingEngine()
    engine.register_liquidity_provider(99)
    interval = _interval()
    engine.submit(
        interval=interval,
        vpp_id=1,
        side="sell",
        purpose=OrderPurpose.BALANCE,
        price=Decimal("50"),
        qty=Decimal("1"),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    peer = engine.submit(
        interval=interval,
        vpp_id=2,
        side="buy",
        purpose=OrderPurpose.BALANCE,
        price=Decimal("60"),
        qty=Decimal("1"),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    assert peer.trades == ()

    grid = engine.submit(
        interval=interval,
        vpp_id=99,
        side="buy",
        purpose=OrderPurpose.SYSTEM_GRID,
        price=Decimal("55"),
        qty=Decimal("1"),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    assert len(grid.trades) == 1
    assert grid.trades[0].sell_vpp_id == 1
    assert grid.trades[0].buy_vpp_id == 99


def test_grid_trade_uses_normal_ledger_and_contract_without_fake_grid_position():
    gateway = TradingGatewayV1()
    interval = _interval()
    gateway.register_system_participant(participant_id=GRID_PARTICIPANT_ID)
    gateway.register_participant(
        participant_id=1,
        params=VPPParams(
            pv_kw_peak=6,
            battery_kwh=0,
            battery_kw_max=0,
            load_kw_base=0,
        ),
    )
    gateway.set_balance_projection(1, interval, 1.0)
    grid = _request(
        GRID_PARTICIPANT_ID,
        "buy",
        "48",
        "1000",
        OrderPurpose.SYSTEM_GRID,
        gateway,
        interval,
    )
    assert not grid.rejected
    seller = _request(1, "sell", "48", "1", OrderPurpose.BALANCE, gateway, interval)
    assert len(seller.trades) == 1
    assert gateway.ledger.balance(1) == Decimal("0.048000")
    assert gateway.ledger.balance(GRID_PARTICIPANT_ID) == Decimal("-0.048000")
    assert gateway.ledger.total() == Decimal("0.000000")
    assert gateway.participants[1].position(interval).contracted_sell_kwh == 1.0
    assert gateway.participants[GRID_PARTICIPANT_ID].positions == {}


def test_system_grid_purpose_is_reserved_for_system_participant():
    gateway = TradingGatewayV1()
    interval = _interval()
    gateway.register_participant(participant_id=1, params=VPPParams())
    outcome = _request(
        1,
        "buy",
        "50",
        "1",
        OrderPurpose.SYSTEM_GRID,
        gateway,
        interval,
    )
    assert len(outcome.rejected) == 1
    assert "reserved" in outcome.rejected[0].reason


def test_simulator_seeds_deep_fallback_grid_without_creating_ranked_vpp(monkeypatch):
    monkeypatch.setenv("EFLUX_MARKET_MODE", "realprice")
    monkeypatch.setenv("EFLUX_EXTERNAL_MARKET_ENABLED", "true")
    monkeypatch.setenv("EFLUX_REALPRICE_FALLBACK_TRADING_ENABLED", "true")
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    products = sim._ensure_products(NOW)
    sim._refresh_realprice_grid(NOW, products)
    snapshot = sim.engine.snapshot(products[0].interval_id, depth_levels=1)
    assert snapshot["best_bid"] == "48.0"
    assert snapshot["best_ask"] == "52.0"
    assert GRID_PARTICIPANT_ID in sim.gateway.participants
    assert GRID_PARTICIPANT_ID not in sim.vpps


def test_disabled_external_market_seeds_no_grid_liquidity(monkeypatch):
    monkeypatch.setenv("EFLUX_MARKET_MODE", "realprice")
    monkeypatch.setenv("EFLUX_EXTERNAL_MARKET_ENABLED", "false")
    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    products = sim._ensure_products(NOW)
    sim._refresh_realprice_grid(NOW, products)
    snapshot = sim.engine.snapshot(products[0].interval_id, depth_levels=1)
    assert snapshot["best_bid"] is None
    assert snapshot["best_ask"] is None
