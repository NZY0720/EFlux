from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from eflux.agents.decision import AgentDecision, CancelRequest, OrderRequest, ReplaceRequest
from eflux.market.delivery import OrderPurpose
from eflux.market.gateway import GatewayRiskLimits, TradingGatewayV2
from eflux.market.ledger import LedgerCategory
from eflux.market.products import DeliveryInterval
from eflux.market.settlement import SettlementPrices
from eflux.vpp.base import VPPParams

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _interval() -> DeliveryInterval:
    start = NOW + timedelta(minutes=5)
    return DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )


def _request(interval, *, side, price="50", qty="1", purpose=OrderPurpose.BALANCE):
    return OrderRequest(side, Decimal(price), Decimal(qty), interval, purpose)


def _gateway() -> TradingGatewayV2:
    gateway = TradingGatewayV2()
    gateway.register_participant(
        participant_id=1,
        params=VPPParams(
            pv_kw_peak=2,
            battery_kwh=1,
            battery_kw_max=4,
            load_kw_base=0,
        ),
    )
    gateway.register_participant(
        participant_id=2,
        params=VPPParams(
            pv_kw_peak=0,
            battery_kwh=1,
            battery_kw_max=4,
            load_kw_base=2,
        ),
    )
    return gateway


def test_full_trade_delivery_and_settlement_reconcile_end_to_end():
    gateway = _gateway()
    interval = _interval()
    gateway.set_balance_projection(1, interval, 1.0)
    gateway.set_balance_projection(2, interval, -1.0)
    sell = gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(orders=(_request(interval, side="sell"),)),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    buy = gateway.execute_decision(
        participant_id=2,
        decision=AgentDecision(orders=(_request(interval, side="buy"),)),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    assert sell.accepted_order_ids
    assert len(buy.trades) == 1
    gateway.close_interval(interval, sim_ts=interval.start, wall_ts=interval.start)
    gateway.record_meter_data(1, interval, renewable_generation_kwh=1.0)
    gateway.record_meter_data(2, interval, load_demand_kwh=1.0)
    prices = SettlementPrices(Decimal("40"), Decimal("100"))
    seller = gateway.settle_participant(1, interval, prices=prices, occurred_at=interval.end)
    buyer = gateway.settle_participant(2, interval, prices=prices, occurred_at=interval.end)
    assert seller.imbalance_kwh == pytest.approx(0.0)
    assert buyer.imbalance_kwh == pytest.approx(0.0)
    assert gateway.ledger.balance(1) == Decimal("0.050000")
    assert gateway.ledger.balance(2) == Decimal("-0.050000")
    assert gateway.ledger.total(category=LedgerCategory.TRADE) == Decimal("0.000000")


def test_resource_rejection_happens_before_order_reaches_book():
    gateway = _gateway()
    interval = _interval()
    # A 4 kW battery has only 0.333 kWh gross energy budget in five minutes.
    result = gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(
            orders=(
                _request(
                    interval,
                    side="sell",
                    qty="0.34",
                    purpose=OrderPurpose.BATTERY,
                ),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    assert not result.accepted_order_ids
    assert "power budget" in result.rejected[0].reason
    assert gateway.engine.snapshot(interval.interval_id)["best_ask"] is None


def test_cancel_releases_unfilled_resource_but_not_filled_commitment():
    gateway = _gateway()
    interval = _interval()
    placed = gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(
            orders=(
                _request(
                    interval,
                    side="sell",
                    qty="0.3",
                    purpose=OrderPurpose.BATTERY,
                ),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    oid = placed.accepted_order_ids[0]
    gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(cancels=(CancelRequest(oid),)),
        sim_ts=NOW + timedelta(seconds=1),
        wall_ts=NOW + timedelta(seconds=1),
    )
    second = gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(
            orders=(
                _request(
                    interval,
                    side="sell",
                    qty="0.3",
                    purpose=OrderPurpose.BATTERY,
                ),
            )
        ),
        sim_ts=NOW + timedelta(seconds=2),
        wall_ts=NOW + timedelta(seconds=2),
    )
    assert second.accepted_order_ids


def test_gas_trade_at_cost_has_zero_profit_after_physical_delivery():
    gateway = TradingGatewayV2()
    gas = VPPParams(
        pv_kw_peak=0,
        battery_kwh=0,
        battery_kw_max=0,
        load_kw_base=0,
        gas_kw_max=12,
        gas_cost_per_mwh=60,
    )
    load = VPPParams(pv_kw_peak=0, battery_kwh=0, battery_kw_max=0, load_kw_base=2)
    gateway.register_participant(participant_id=1, params=gas)
    gateway.register_participant(participant_id=2, params=load)
    interval = _interval()
    gateway.set_balance_projection(2, interval, -1.0)
    gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(
            orders=(
                _request(
                    interval,
                    side="sell",
                    price="60",
                    purpose=OrderPurpose.DISPATCHABLE,
                ),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    gateway.execute_decision(
        participant_id=2,
        decision=AgentDecision(orders=(_request(interval, side="buy", price="60"),)),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    gateway.close_interval(interval, sim_ts=interval.start, wall_ts=interval.start)
    gateway.record_meter_data(1, interval)
    gateway.record_meter_data(2, interval, load_demand_kwh=1.0)
    prices = SettlementPrices(Decimal("40"), Decimal("100"))
    gateway.settle_participant(1, interval, prices=prices, occurred_at=interval.end)
    gateway.settle_participant(2, interval, prices=prices, occurred_at=interval.end)
    assert gateway.ledger.balance(1) == Decimal("0.000000")


def test_under_delivery_is_charged_at_short_imbalance_price():
    gateway = _gateway()
    interval = _interval()
    gateway.set_balance_projection(1, interval, 1.0)
    gateway.set_balance_projection(2, interval, -1.0)
    gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(orders=(_request(interval, side="sell"),)),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    gateway.execute_decision(
        participant_id=2,
        decision=AgentDecision(orders=(_request(interval, side="buy"),)),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    gateway.close_interval(interval, sim_ts=interval.start, wall_ts=interval.start)
    gateway.record_meter_data(1, interval, renewable_generation_kwh=0.75)
    result = gateway.settle_participant(
        1,
        interval,
        prices=SettlementPrices(Decimal("40"), Decimal("100")),
        occurred_at=interval.end,
    )
    assert result.imbalance_kwh == pytest.approx(-0.25)
    # Trade revenue 0.05 minus short settlement 0.025.
    assert gateway.ledger.balance(1) == Decimal("0.025000")


def test_replace_reuses_credit_reserved_by_original_order():
    gateway = TradingGatewayV2(limits=GatewayRiskLimits(credit_limit_usd=Decimal("0.05")))
    gateway.register_participant(
        participant_id=1,
        params=VPPParams(
            pv_kw_peak=0,
            battery_kwh=0,
            battery_kw_max=0,
            load_kw_base=1,
            starting_cash_usd=0,
        ),
    )
    interval = _interval()
    gateway.set_balance_projection(1, interval, -1.0)
    placed = gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(orders=(_request(interval, side="buy"),)),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    replaced = gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(
            replaces=(
                ReplaceRequest(
                    placed.accepted_order_ids[0],
                    _request(interval, side="buy", price="49"),
                ),
            )
        ),
        sim_ts=NOW + timedelta(seconds=1),
        wall_ts=NOW + timedelta(seconds=1),
    )
    assert replaced.accepted_order_ids
    assert not replaced.rejected


def test_flexible_load_fill_becomes_physical_consumption():
    gateway = _gateway()
    interval = _interval()
    gateway.set_balance_projection(1, interval, 0.5)
    gateway.set_flex_load_capacity(2, interval, 0.5)
    gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(orders=(_request(interval, side="sell", qty="0.5"),)),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    bought = gateway.execute_decision(
        participant_id=2,
        decision=AgentDecision(
            orders=(
                _request(
                    interval,
                    side="buy",
                    qty="0.5",
                    purpose=OrderPurpose.FLEX_LOAD,
                ),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    assert len(bought.trades) == 1
    gateway.close_interval(interval, sim_ts=interval.start, wall_ts=interval.start)
    gateway.record_meter_data(1, interval, renewable_generation_kwh=0.5)
    gateway.record_meter_data(2, interval)
    prices = SettlementPrices(Decimal("40"), Decimal("100"))
    gateway.settle_participant(1, interval, prices=prices, occurred_at=interval.end)
    result = gateway.settle_participant(2, interval, prices=prices, occurred_at=interval.end)
    position = gateway.participants[2].positions[interval.interval_id]
    assert position.flexible_load_demand_kwh == pytest.approx(0.5)
    assert result.imbalance_kwh == pytest.approx(0.0)


def test_gateway_charges_gas_startup_once_on_transition_from_off():
    gateway = TradingGatewayV2()
    gateway.register_participant(
        participant_id=1,
        params=VPPParams(
            pv_kw_peak=0,
            battery_kwh=0,
            battery_kw_max=0,
            load_kw_base=0,
            gas_kw_max=12,
            gas_cost_per_mwh=60,
            gas_startup_cost_usd=0.25,
        ),
    )
    gateway.register_participant(
        participant_id=2,
        params=VPPParams(
            pv_kw_peak=0,
            battery_kwh=0,
            battery_kw_max=0,
            load_kw_base=2,
        ),
    )
    interval = _interval()
    gateway.set_balance_projection(2, interval, -1.0)
    gateway.execute_decision(
        participant_id=1,
        decision=AgentDecision(
            orders=(
                _request(
                    interval,
                    side="sell",
                    price="60",
                    purpose=OrderPurpose.DISPATCHABLE,
                ),
            )
        ),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    gateway.execute_decision(
        participant_id=2,
        decision=AgentDecision(orders=(_request(interval, side="buy", price="60"),)),
        sim_ts=NOW,
        wall_ts=NOW,
    )
    gateway.close_interval(interval, sim_ts=interval.start, wall_ts=interval.start)
    gateway.settle_participant(
        1,
        interval,
        prices=SettlementPrices(Decimal("40"), Decimal("100")),
        occurred_at=interval.end,
    )
    assert gateway.ledger.breakdown(1)[LedgerCategory.DISPATCHABLE_STARTUP] == Decimal("-0.250000")
