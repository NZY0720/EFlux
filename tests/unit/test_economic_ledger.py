from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from eflux.market.ledger import EconomicLedger, LedgerCategory, usd_for_energy
from eflux.market.products import DeliveryInterval


def _interval() -> DeliveryInterval:
    start = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
    return DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )


def test_usd_conversion_uses_real_dollars():
    assert usd_for_energy(Decimal("50"), Decimal("1")) == Decimal("0.050000")


def test_trade_entries_conserve_cash_before_fees():
    ledger = EconomicLedger()
    buyer, seller = ledger.post_trade(
        buyer_id=1,
        seller_id=2,
        price_per_mwh=Decimal("50"),
        qty_kwh=Decimal("2"),
        occurred_at=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
        interval=_interval(),
        trade_id="trade-1",
    )
    assert buyer.amount_usd == Decimal("-0.100000")
    assert seller.amount_usd == Decimal("0.100000")
    assert ledger.total(category=LedgerCategory.TRADE) == Decimal("0.000000")


def test_negative_price_reverses_trade_cash_direction_without_special_case():
    ledger = EconomicLedger()
    buyer, seller = ledger.post_trade(
        buyer_id=1,
        seller_id=2,
        price_per_mwh=Decimal("-40"),
        qty_kwh=Decimal("1"),
        occurred_at=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
        interval=_interval(),
        trade_id="trade-negative",
    )
    assert buyer.amount_usd == Decimal("0.040000")
    assert seller.amount_usd == Decimal("-0.040000")
    assert ledger.total() == Decimal("0.000000")


def test_cost_breakdown_reconciles_to_participant_balance():
    ledger = EconomicLedger()
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    ledger.post(
        participant_id=2,
        category=LedgerCategory.TRADE,
        amount_usd=Decimal("1.0"),
        occurred_at=now,
    )
    ledger.post(
        participant_id=2,
        category=LedgerCategory.FUEL,
        amount_usd=Decimal("-0.4"),
        occurred_at=now,
    )
    ledger.post(
        participant_id=2,
        category=LedgerCategory.IMBALANCE,
        amount_usd=Decimal("-0.1"),
        occurred_at=now,
    )
    assert ledger.balance(2) == Decimal("0.500000")
    assert sum(ledger.breakdown(2).values(), Decimal("0")) == ledger.balance(2)
