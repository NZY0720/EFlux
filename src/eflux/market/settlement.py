"""Interval settlement into the append-only V1 economic ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from eflux.market.delivery import DeliveryPosition
from eflux.market.ledger import EconomicLedger, LedgerCategory, usd_for_energy

BALANCING_AUTHORITY_ID = 0


@dataclass(frozen=True, slots=True)
class SettlementPrices:
    """Economic prices for one interval, all in real USD/MWh."""

    long_imbalance_price: Decimal
    short_imbalance_price: Decimal
    curtailment_price: Decimal = Decimal("0")
    value_of_lost_load: Decimal = Decimal("10000")

    def __post_init__(self) -> None:
        for name in (
            "long_imbalance_price",
            "short_imbalance_price",
            "curtailment_price",
            "value_of_lost_load",
        ):
            if not getattr(self, name).is_finite():
                raise ValueError(f"{name} must be finite")
        if self.value_of_lost_load < 0:
            raise ValueError("value_of_lost_load must be non-negative")


@dataclass(frozen=True, slots=True)
class SettlementResult:
    participant_id: int
    interval_id: str
    imbalance_kwh: float
    imbalance_usd: Decimal
    fuel_cost_usd: Decimal
    startup_cost_usd: Decimal
    degradation_cost_usd: Decimal
    curtailment_usd: Decimal
    unserved_load_cost_usd: Decimal

    @property
    def economic_delta_usd(self) -> Decimal:
        return (
            self.imbalance_usd
            - self.fuel_cost_usd
            - self.startup_cost_usd
            - self.degradation_cost_usd
            + self.curtailment_usd
            - self.unserved_load_cost_usd
        )


def settle_delivery_position(
    ledger: EconomicLedger,
    *,
    participant_id: int,
    position: DeliveryPosition,
    prices: SettlementPrices,
    occurred_at: datetime,
    fuel_cost_per_mwh: Decimal = Decimal("0"),
    dispatchable_startup_cost_usd: Decimal = Decimal("0"),
    battery_degradation_cost_per_mwh_throughput: Decimal = Decimal("0"),
    battery_cell_throughput_kwh: Decimal = Decimal("0"),
) -> SettlementResult:
    """Book non-trade economics after physical delivery is complete.

    Trade cash is posted when a fill creates the contract.  This function books
    the later physical consequences: balancing energy, fuel, degradation,
    curtailment, and unserved-load welfare loss.
    """

    position.validate()
    for name, value in (
        ("fuel_cost_per_mwh", fuel_cost_per_mwh),
        ("dispatchable_startup_cost_usd", dispatchable_startup_cost_usd),
        (
            "battery_degradation_cost_per_mwh_throughput",
            battery_degradation_cost_per_mwh_throughput,
        ),
        ("battery_cell_throughput_kwh", battery_cell_throughput_kwh),
    ):
        if not value.is_finite() or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")

    imbalance = position.imbalance_kwh
    imbalance_price = (
        prices.long_imbalance_price if imbalance >= 0.0 else prices.short_imbalance_price
    )
    imbalance_value = usd_for_energy(imbalance_price, Decimal(str(abs(imbalance))))
    imbalance_usd = imbalance_value if imbalance >= 0.0 else -imbalance_value
    fuel_cost = usd_for_energy(
        fuel_cost_per_mwh, Decimal(str(position.dispatchable_generation_kwh))
    )
    startup_cost = dispatchable_startup_cost_usd.quantize(Decimal("0.000001"))
    degradation = usd_for_energy(
        battery_degradation_cost_per_mwh_throughput, battery_cell_throughput_kwh
    )
    curtailment = usd_for_energy(
        prices.curtailment_price, Decimal(str(position.curtailed_generation_kwh))
    )
    unserved = usd_for_energy(prices.value_of_lost_load, Decimal(str(position.unserved_load_kwh)))

    interval = position.interval
    iid = interval.interval_id
    if imbalance_usd:
        ledger.post(
            participant_id=participant_id,
            category=LedgerCategory.IMBALANCE,
            amount_usd=imbalance_usd,
            occurred_at=occurred_at,
            interval=interval,
            reference_id=iid,
        )
        # Balancing energy is a cash transfer, so book the counter-entry too.
        ledger.post(
            participant_id=BALANCING_AUTHORITY_ID,
            category=LedgerCategory.IMBALANCE,
            amount_usd=-imbalance_usd,
            occurred_at=occurred_at,
            interval=interval,
            reference_id=iid,
        )
    if fuel_cost:
        ledger.post(
            participant_id=participant_id,
            category=LedgerCategory.FUEL,
            amount_usd=-fuel_cost,
            occurred_at=occurred_at,
            interval=interval,
            reference_id=iid,
        )
    if startup_cost:
        ledger.post(
            participant_id=participant_id,
            category=LedgerCategory.DISPATCHABLE_STARTUP,
            amount_usd=-startup_cost,
            occurred_at=occurred_at,
            interval=interval,
            reference_id=iid,
        )
    if degradation:
        ledger.post(
            participant_id=participant_id,
            category=LedgerCategory.BATTERY_DEGRADATION,
            amount_usd=-degradation,
            occurred_at=occurred_at,
            interval=interval,
            reference_id=iid,
        )
    if curtailment:
        ledger.post(
            participant_id=participant_id,
            category=LedgerCategory.CURTAILMENT,
            amount_usd=curtailment,
            occurred_at=occurred_at,
            interval=interval,
            reference_id=iid,
        )
    if unserved:
        ledger.post(
            participant_id=participant_id,
            category=LedgerCategory.UNSERVED_LOAD,
            amount_usd=-unserved,
            occurred_at=occurred_at,
            interval=interval,
            reference_id=iid,
        )
    return SettlementResult(
        participant_id=participant_id,
        interval_id=iid,
        imbalance_kwh=imbalance,
        imbalance_usd=imbalance_usd,
        fuel_cost_usd=fuel_cost,
        startup_cost_usd=startup_cost,
        degradation_cost_usd=degradation,
        curtailment_usd=curtailment,
        unserved_load_cost_usd=unserved,
    )
