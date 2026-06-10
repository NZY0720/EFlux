"""Zero-Intelligence agent (Gode & Sunder 1993).

Generates random orders constrained only by individual rationality:
- A seller never offers below its reservation price (here: a fraction of price_ref).
- A buyer never bids above its reservation price.

Side and quantity follow the VPP's net position (surplus → sell, deficit → buy).
Price is uniform-random within the rational range.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import AgentContext, BaseAgent, OrderIntent


@dataclass
class ZIAgent(BaseAgent):
    price_ref: Decimal = Decimal("50.0")
    spread_frac: float = 0.5  # half-width of rational price range, as fraction of price_ref
    min_qty: Decimal = Decimal("0.01")

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        # Determine direction from the accumulated untraded balance (the runner
        # credits per-tick net energy into pending_net_kwh; quoting per-tick
        # slivers would never clear min_qty with a 1s tick).
        net_kwh = ctx.state.pending_net_kwh
        # Add a small battery contribution proportional to SOC headroom.
        batt_room = ctx.battery.capacity_kwh - ctx.battery.soc_kwh
        batt_kwh = ctx.battery.max_power_kw * ctx.tick_duration_h

        if net_kwh > 0:
            qty = net_kwh + 0.5 * batt_kwh * ctx.battery.soc_frac
            side = "sell"
        elif net_kwh < 0:
            qty = -net_kwh + 0.5 * batt_kwh * (batt_room / ctx.battery.capacity_kwh)
            side = "buy"
        else:
            return []
        if qty < float(self.min_qty):
            return []  # keep accumulating until the order is worth placing

        # Uniform random price in rational range.
        ref = float(self.price_ref)
        spread = ref * self.spread_frac
        if side == "sell":
            # Seller: rational range [ref * (1 - spread), ref * (1 + spread)],
            # but reservation = floor at marginal cost ≈ ref * (1 - spread).
            lo = ref - spread
            hi = ref + spread
        else:
            lo = ref - spread
            hi = ref + spread
        price = Decimal(str(ctx.rng.uniform(lo, hi))).quantize(Decimal("0.0001"))
        qty_d = Decimal(str(qty)).quantize(Decimal("0.0001"))
        if qty_d < self.min_qty:
            return []
        return [OrderIntent(side=side, price=price, qty=qty_d)]
