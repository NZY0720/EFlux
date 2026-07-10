"""Gas generator agent — dispatchable supply at marginal cost.

A gas plant has no ambient energy balance to trade: it simply offers its
capacity at its fuel marginal cost plus any startup-cost adder and generates
whatever fills. Quotes carry ``purpose=dispatchable`` so the gateway reserves
capacity and ramp explicitly. Economically this puts a soft price cap on the market:
whenever bids rise above gas marginal cost, gas supply gets dispatched
(merit order: renewables ~floor < battery ~52.7 < gas 55-72).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from eflux.agents.base import AgentContext, BaseAgent
from eflux.agents.decision import AgentDecision, OrderRequest
from eflux.market.delivery import OrderPurpose


@dataclass
class GasGeneratorAgent(BaseAgent):
    min_qty: Decimal = Decimal("0.01")

    def decide(self, ctx: AgentContext) -> AgentDecision:
        cap_kw = ctx.params.gas_kw_max
        if cap_kw <= 0:
            return AgentDecision.hold("no dispatchable capacity")

        qty_f = cap_kw * ctx.primary_interval.duration_h
        qty = Decimal(str(qty_f)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        if qty < self.min_qty:
            return AgentDecision.hold("dispatchable interval capacity below minimum quantity")
        price = Decimal(str(ctx.params.gas_cost_per_mwh))
        if ctx.dispatchable_power_kw <= 1e-9 and ctx.params.gas_startup_cost_usd > 0.0:
            price += Decimal(str(ctx.params.gas_startup_cost_usd)) * Decimal("1000") / qty
        price = price.quantize(Decimal("0.0001"))
        return AgentDecision(
            orders=(
                OrderRequest(
                    side="sell",
                    price=price,
                    qty_kwh=qty,
                    interval=ctx.primary_interval,
                    purpose=OrderPurpose.DISPATCHABLE,
                    ttl_sec=ctx.decision_interval_sec,
                ),
            )
        )
