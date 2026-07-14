"""Append-only real-USD economic ledger for market V1.

Prices are quoted in USD/MWh and quantities in kWh.  Every conversion to cash
therefore divides by 1000 at the point an entry is created.  The ledger uses a
single sign convention: positive amounts credit the participant, negative
amounts debit it.  Negative energy prices naturally reverse payer/payee signs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from eflux.market.products import DeliveryInterval

KWH_PER_MWH = Decimal("1000")
MONEY_QUANT = Decimal("0.000001")


def usd_for_energy(price_per_mwh: Decimal, energy_kwh: Decimal) -> Decimal:
    """Cash value of terminal energy, preserving the sign of price and quantity."""

    if not price_per_mwh.is_finite() or not energy_kwh.is_finite():
        raise ValueError("price and energy must be finite")
    if energy_kwh < 0:
        raise ValueError("energy_kwh must be non-negative; direction belongs in the entry sign")
    return (price_per_mwh * energy_kwh / KWH_PER_MWH).quantize(MONEY_QUANT)


class LedgerCategory(StrEnum):
    TRADE = "trade"
    TRANSACTION_FEE = "transaction_fee"
    MESSAGE_FEE = "message_fee"
    FUEL = "fuel"
    DISPATCHABLE_STARTUP = "dispatchable_startup"
    BATTERY_DEGRADATION = "battery_degradation"
    IMBALANCE = "imbalance"
    CURTAILMENT = "curtailment"
    UNSERVED_LOAD = "unserved_load"
    INITIAL_INVENTORY = "initial_inventory"
    TERMINAL_INVENTORY = "terminal_inventory"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_id: int
    participant_id: int
    category: LedgerCategory
    amount_usd: Decimal
    occurred_at: datetime
    interval_id: str | None = None
    reference_id: str | None = None
    detail: str = ""


@dataclass(slots=True)
class EconomicLedger:
    """In-memory append-only ledger; persistence subscribes to the same entries later."""

    entries: list[LedgerEntry] = field(default_factory=list)
    _next_entry_id: int = 1

    def post(
        self,
        *,
        participant_id: int,
        category: LedgerCategory,
        amount_usd: Decimal,
        occurred_at: datetime,
        interval: DeliveryInterval | None = None,
        reference_id: str | None = None,
        detail: str = "",
    ) -> LedgerEntry:
        if not amount_usd.is_finite():
            raise ValueError("amount_usd must be finite")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        entry = LedgerEntry(
            entry_id=self._next_entry_id,
            participant_id=participant_id,
            category=category,
            amount_usd=amount_usd.quantize(MONEY_QUANT),
            occurred_at=occurred_at.astimezone(UTC),
            interval_id=None if interval is None else interval.interval_id,
            reference_id=reference_id,
            detail=detail,
        )
        self._next_entry_id += 1
        self.entries.append(entry)
        return entry

    def post_trade(
        self,
        *,
        buyer_id: int,
        seller_id: int,
        price_per_mwh: Decimal,
        qty_kwh: Decimal,
        occurred_at: datetime,
        interval: DeliveryInterval,
        trade_id: str,
    ) -> tuple[LedgerEntry, LedgerEntry]:
        value = usd_for_energy(price_per_mwh, qty_kwh)
        buyer = self.post(
            participant_id=buyer_id,
            category=LedgerCategory.TRADE,
            amount_usd=-value,
            occurred_at=occurred_at,
            interval=interval,
            reference_id=trade_id,
            detail=f"buy {qty_kwh} kWh @ {price_per_mwh} USD/MWh",
        )
        seller = self.post(
            participant_id=seller_id,
            category=LedgerCategory.TRADE,
            amount_usd=value,
            occurred_at=occurred_at,
            interval=interval,
            reference_id=trade_id,
            detail=f"sell {qty_kwh} kWh @ {price_per_mwh} USD/MWh",
        )
        return buyer, seller

    def balance(self, participant_id: int) -> Decimal:
        return sum(
            (e.amount_usd for e in self.entries if e.participant_id == participant_id),
            Decimal("0"),
        ).quantize(MONEY_QUANT)

    def breakdown(self, participant_id: int) -> dict[LedgerCategory, Decimal]:
        out: dict[LedgerCategory, Decimal] = {}
        for entry in self.entries:
            if entry.participant_id != participant_id:
                continue
            out[entry.category] = out.get(entry.category, Decimal("0")) + entry.amount_usd
        return {category: amount.quantize(MONEY_QUANT) for category, amount in out.items()}

    def total(self, *, category: LedgerCategory | None = None) -> Decimal:
        return sum(
            (e.amount_usd for e in self.entries if category is None or e.category == category),
            Decimal("0"),
        ).quantize(MONEY_QUANT)
