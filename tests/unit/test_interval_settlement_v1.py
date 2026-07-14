from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from eflux.market.delivery import DeliveryPosition
from eflux.market.ledger import EconomicLedger, LedgerCategory
from eflux.market.products import DeliveryInterval
from eflux.market.settlement import SettlementPrices, settle_delivery_position


def _interval() -> DeliveryInterval:
    start = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
    return DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )


def test_short_imbalance_is_a_debit_and_balancing_cash_conserves():
    ledger = EconomicLedger()
    position = DeliveryPosition(_interval(), renewable_generation_kwh=0.75, contracted_sell_kwh=1.0)
    result = settle_delivery_position(
        ledger,
        participant_id=1,
        position=position,
        prices=SettlementPrices(Decimal("40"), Decimal("100")),
        occurred_at=position.interval.end,
    )
    assert result.imbalance_kwh == -0.25
    assert result.imbalance_usd == Decimal("-0.025000")
    assert ledger.total(category=LedgerCategory.IMBALANCE) == Decimal("0.000000")


def test_negative_long_price_charges_over_delivery():
    ledger = EconomicLedger()
    position = DeliveryPosition(_interval(), renewable_generation_kwh=1.0)
    result = settle_delivery_position(
        ledger,
        participant_id=1,
        position=position,
        prices=SettlementPrices(Decimal("-50"), Decimal("60")),
        occurred_at=position.interval.end,
    )
    assert result.imbalance_usd == Decimal("-0.050000")


def test_gas_revenue_at_marginal_cost_has_zero_economic_profit():
    ledger = EconomicLedger()
    interval = _interval()
    ledger.post_trade(
        buyer_id=2,
        seller_id=1,
        price_per_mwh=Decimal("60"),
        qty_kwh=Decimal("1"),
        occurred_at=interval.gate_closure - timedelta(seconds=1),
        interval=interval,
        trade_id="gas-trade",
    )
    position = DeliveryPosition(interval, dispatchable_generation_kwh=1.0, contracted_sell_kwh=1.0)
    result = settle_delivery_position(
        ledger,
        participant_id=1,
        position=position,
        prices=SettlementPrices(Decimal("40"), Decimal("100")),
        occurred_at=interval.end,
        fuel_cost_per_mwh=Decimal("60"),
    )
    assert result.fuel_cost_usd == Decimal("0.060000")
    assert ledger.balance(1) == Decimal("0.000000")


def test_dispatchable_startup_cost_is_booked_separately():
    ledger = EconomicLedger()
    position = DeliveryPosition(_interval(), dispatchable_generation_kwh=1.0)
    result = settle_delivery_position(
        ledger,
        participant_id=1,
        position=position,
        prices=SettlementPrices(Decimal("0"), Decimal("100")),
        occurred_at=position.interval.end,
        dispatchable_startup_cost_usd=Decimal("0.25"),
    )
    assert result.startup_cost_usd == Decimal("0.250000")
    assert ledger.breakdown(1)[LedgerCategory.DISPATCHABLE_STARTUP] == Decimal("-0.250000")


def test_degradation_and_unserved_load_are_explicit_economic_costs():
    ledger = EconomicLedger()
    position = DeliveryPosition(
        _interval(),
        load_demand_kwh=1.0,
        unserved_load_kwh=0.1,
        battery_discharge_terminal_kwh=0.9,
    )
    result = settle_delivery_position(
        ledger,
        participant_id=1,
        position=position,
        prices=SettlementPrices(Decimal("40"), Decimal("100"), value_of_lost_load=Decimal("10000")),
        occurred_at=position.interval.end,
        battery_degradation_cost_per_mwh_throughput=Decimal("20"),
        battery_cell_throughput_kwh=Decimal("1"),
    )
    assert result.degradation_cost_usd == Decimal("0.020000")
    assert result.unserved_load_cost_usd == Decimal("1.000000")
    assert ledger.balance(1) == Decimal("-1.020000")
