"""Physical and contractual energy accounting for one delivery interval.

Sign convention (all values are terminal kWh):

* physical net injection: export to the market is positive;
* contracted net injection: sells are positive, buys are negative;
* imbalance: physical minus contracted.  Positive means long/over-delivered,
  negative means short/under-delivered.

SOC is deliberately absent here.  Battery cell-energy accounting belongs to
the battery/reservation layer; this position records only terminal energy that
actually crossed the VPP meter during the interval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from eflux.market.products import DeliveryInterval


class OrderPurpose(StrEnum):
    """The physical source/sink backing an order, replacing ``dispatched: bool``."""

    BALANCE = "balance"
    BATTERY = "battery"
    DISPATCHABLE = "dispatchable"
    FLEX_LOAD = "flex_load"


@dataclass(slots=True)
class DeliveryPosition:
    """Auditable energy balance for one participant and delivery product."""

    interval: DeliveryInterval
    renewable_generation_kwh: float = 0.0
    load_demand_kwh: float = 0.0
    curtailed_generation_kwh: float = 0.0
    unserved_load_kwh: float = 0.0
    battery_charge_terminal_kwh: float = 0.0
    battery_discharge_terminal_kwh: float = 0.0
    dispatchable_generation_kwh: float = 0.0
    contracted_buy_kwh: float = 0.0
    contracted_sell_kwh: float = 0.0

    def record_contract(self, *, side: str, qty_kwh: float) -> None:
        self._require_nonnegative(qty_kwh, "qty_kwh")
        if side == "buy":
            self.contracted_buy_kwh += qty_kwh
        elif side == "sell":
            self.contracted_sell_kwh += qty_kwh
        else:
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    @property
    def served_load_kwh(self) -> float:
        return self.load_demand_kwh - self.unserved_load_kwh

    @property
    def delivered_renewable_kwh(self) -> float:
        return self.renewable_generation_kwh - self.curtailed_generation_kwh

    @property
    def physical_net_injection_kwh(self) -> float:
        return (
            self.delivered_renewable_kwh
            + self.dispatchable_generation_kwh
            + self.battery_discharge_terminal_kwh
            - self.served_load_kwh
            - self.battery_charge_terminal_kwh
        )

    @property
    def contracted_net_injection_kwh(self) -> float:
        return self.contracted_sell_kwh - self.contracted_buy_kwh

    @property
    def imbalance_kwh(self) -> float:
        return self.physical_net_injection_kwh - self.contracted_net_injection_kwh

    def validate(self, *, tolerance: float = 1e-9) -> None:
        for field in (
            "renewable_generation_kwh",
            "load_demand_kwh",
            "curtailed_generation_kwh",
            "unserved_load_kwh",
            "battery_charge_terminal_kwh",
            "battery_discharge_terminal_kwh",
            "dispatchable_generation_kwh",
            "contracted_buy_kwh",
            "contracted_sell_kwh",
        ):
            self._require_nonnegative(getattr(self, field), field)
        if self.curtailed_generation_kwh > self.renewable_generation_kwh + tolerance:
            raise ValueError("curtailed generation cannot exceed renewable generation")
        if self.unserved_load_kwh > self.load_demand_kwh + tolerance:
            raise ValueError("unserved load cannot exceed load demand")

    @staticmethod
    def _require_nonnegative(value: float, field: str) -> None:
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"{field} must be finite and non-negative")
