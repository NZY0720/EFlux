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

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        # Quote from the accumulated untraded balance, not this tick's sliver of
        # energy: with a 1s tick the per-tick net is ~1e-3 kWh and would never
        # clear min_qty. The runner maintains pending_net_kwh across ticks.
        net_kwh = ctx.state.pending_net_kwh
        if abs(net_kwh) < float(self.min_qty):
            return []

        eta = max(0.01, ctx.battery.eta_rt)
        sqrt_eta = math.sqrt(eta)
        battery_sell_price = float(self.price_ref) / sqrt_eta  # cost to deliver from battery
        battery_buy_price = float(self.price_ref) * sqrt_eta  # value of storing to battery

        side: str
        price_f: float
        qty_f: float

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
            # Optional shave for risk-averse agents (they bid below price_ref).
            price_f *= 1.0 - float(ctx.params.markup_ceiling) * (1.0 - float(ctx.params.risk_aversion)) * 0.0
            qty_f = -net_kwh

        price = Decimal(str(price_f)).quantize(Decimal("0.0001"))
        qty = Decimal(str(qty_f)).quantize(Decimal("0.0001"))
        if price <= 0 or qty < self.min_qty:
            return []
        return [OrderIntent(side=side, price=price, qty=qty)]
