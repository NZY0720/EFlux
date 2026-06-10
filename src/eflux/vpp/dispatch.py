"""Internal heuristic dispatcher: maps market price signal + DER state → buy/sell intent.

This is intentionally simple. RL agent (Phase 5) replaces this for the inner loop.
Rule of thumb:
- If net_kw > 0 (surplus): sell at marginal opportunity cost = forecast future price.
- If net_kw < 0 (deficit): buy up to need.
- Battery: charge when price is low + room available; discharge when price is high + SOC available.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class DispatchDecision:
    side: str  # "buy" or "sell" or "none"
    qty_kwh: float
    reservation_price: Decimal  # min sell price OR max buy price


@dataclass
class HeuristicDispatcher:
    price_ref: Decimal = Decimal("50.0")  # reference price (currency / MWh)
    low_threshold_frac: float = 0.7  # below this fraction of price_ref -> aggressive buy
    high_threshold_frac: float = 1.3  # above this fraction of price_ref -> aggressive sell

    def decide(
        self,
        *,
        net_kw: float,
        soc_frac: float,
        battery_kw_max: float,
        tick_duration_h: float,
    ) -> DispatchDecision:
        # Convert net_kW for this tick to kWh.
        net_kwh = net_kw * tick_duration_h
        # Headroom-aware battery contribution.
        batt_kwh = battery_kw_max * tick_duration_h
        if net_kwh > 0:
            qty = net_kwh + 0.5 * batt_kwh * soc_frac  # also discharge a bit if SOC high
            reservation = self.price_ref * Decimal(str(self.low_threshold_frac))
            return DispatchDecision(side="sell", qty_kwh=qty, reservation_price=reservation)
        if net_kwh < 0:
            qty = -net_kwh + 0.5 * batt_kwh * (1 - soc_frac)  # also charge a bit if SOC low
            reservation = self.price_ref * Decimal(str(self.high_threshold_frac))
            return DispatchDecision(side="buy", qty_kwh=qty, reservation_price=reservation)
        return DispatchDecision(side="none", qty_kwh=0.0, reservation_price=self.price_ref)
