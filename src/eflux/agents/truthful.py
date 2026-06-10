"""Truthful (cost-based) agent.

Reports its true marginal cost (sell side) or marginal value (buy side) every tick.
No random noise, no strategic shading. Useful as a baseline against ZI/PPO to verify
that smarter strategies actually improve PnL.

Marginal cost / value model
---------------------------
- PV surplus has a marginal cost of ~0 (free electricity) and should be offered at
  `markup_floor * price_ref` (minimum acceptable price).
- Battery discharge cost: the round-trip efficiency means each kWh sold from the
  battery cost `price_ref / sqrt(eta_rt)` to put in there — so the seller wants at
  least that price, plus a configurable floor.
- Battery charge value: each kWh stored is worth `price_ref * sqrt(eta_rt)` later
  (you'll only recover that fraction when discharging), so the buyer is willing to
  pay up to that.
- Direct load coverage: paying up to `price_ref` is rational (the alternative is
  paying the grid retail rate, which we model as `price_ref`).

Side choice mirrors ZI: positive net energy ⇒ sell; negative ⇒ buy; balanced ⇒ no order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import AgentContext, BaseAgent, OrderIntent


@dataclass
class TruthfulAgent(BaseAgent):
    price_ref: Decimal = Decimal("50.0")
    min_qty: Decimal = Decimal("0.01")
    # Battery arbitrage band. Above soc_high the agent offers stored energy at
    # its delivery cost; below soc_low it bids to recharge at its storage
    # value. Without this, nighttime (PV=0) leaves every VPP in deficit — a
    # market with only buyers and zero trades. The band straddles the 50%
    # boot SOC so a fresh market has supply from the first minute.
    soc_high: float = 0.45
    soc_low: float = 0.25
    battery_quote_every_n_ticks: int = 30
    _ticks_since_battery_quote: int = 0

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        eta = max(0.01, ctx.battery.eta_rt)
        sqrt_eta = math.sqrt(eta)
        battery_sell_price = float(self.price_ref) / sqrt_eta  # cost to deliver from battery
        battery_buy_price = float(self.price_ref) * sqrt_eta  # value of storing to battery

        intents: list[OrderIntent] = []

        # 1) Quote the accumulated untraded balance, not this tick's sliver of
        # energy: with a 1s tick the per-tick net is ~1e-3 kWh and would never
        # clear min_qty. The runner maintains pending_net_kwh across ticks.
        net_kwh = ctx.state.pending_net_kwh
        if abs(net_kwh) >= float(self.min_qty):
            if net_kwh > 0:
                side = "sell"
                # Surplus sourced from PV (load fully covered) has marginal cost ≈ 0 →
                # quote floor. Surplus beyond what PV currently produces would come out
                # of the battery → quote the battery delivery cost.
                if ctx.state.net_kw <= ctx.state.pv_kw:
                    # Pure PV export — quote the floor (markup_floor * price_ref).
                    price_f = max(float(ctx.params.markup_floor) * float(self.price_ref), 0.0001)
                else:
                    price_f = battery_sell_price
                qty_f = net_kwh
            else:
                side = "buy"
                # Deficit: pay up to price_ref to cover load directly. If battery has room,
                # we'd also be willing to pay battery_buy_price for storage, which is
                # strictly lower than price_ref — so for a single quote, use price_ref.
                price_f = float(self.price_ref)
                qty_f = -net_kwh

            price = Decimal(str(price_f)).quantize(Decimal("0.0001"))
            qty = Decimal(str(qty_f)).quantize(Decimal("0.0001"))
            if price > 0 and qty >= self.min_qty:
                intents.append(OrderIntent(side=side, price=price, qty=qty))

        # 2) Battery-band arbitrage quote (throttled). Sized to what the battery
        # could physically deliver over the cooldown window, capped by the SOC
        # distance to the band edge so it self-limits as fills move the SOC.
        self._ticks_since_battery_quote += 1
        if self._ticks_since_battery_quote >= self.battery_quote_every_n_ticks:
            block = ctx.battery.max_power_kw * ctx.tick_duration_h * self.battery_quote_every_n_ticks
            soc = ctx.battery.soc_frac
            cap = ctx.battery.capacity_kwh
            batt_side: str | None = None
            if soc > self.soc_high:
                batt_side = "sell"
                batt_qty = min(block, (soc - self.soc_high) * cap)
                batt_price = battery_sell_price
            elif soc < self.soc_low:
                batt_side = "buy"
                batt_qty = min(block, (self.soc_low - soc) * cap)
                batt_price = battery_buy_price
            if batt_side is not None and batt_qty >= float(self.min_qty):
                self._ticks_since_battery_quote = 0
                intents.append(
                    OrderIntent(
                        side=batt_side,
                        price=Decimal(str(batt_price)).quantize(Decimal("0.0001")),
                        qty=Decimal(str(batt_qty)).quantize(Decimal("0.0001")),
                        from_battery=True,
                    )
                )

        return intents
