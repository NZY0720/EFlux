"""Gas generator agent — dispatchable supply at marginal cost.

A gas plant has no ambient energy balance to trade: it simply offers its
capacity at its fuel marginal cost (params.gas_cost_per_kwh) and generates
whatever fills. Quotes carry dispatched=True so the runner doesn't debit
pending_net_kwh. Economically this puts a soft price cap on the market:
whenever bids rise above gas marginal cost, gas supply gets dispatched
(merit order: renewables ~floor < battery ~52.7 < gas 55-72).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import AgentContext, BaseAgent, OrderIntent


@dataclass
class GasGeneratorAgent(BaseAgent):
    quote_every_n_ticks: int = 30
    min_qty: Decimal = Decimal("0.01")
    _ticks_since_quote: int = 0

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        cap_kw = ctx.params.gas_kw_max
        if cap_kw <= 0:
            return []
        self._ticks_since_quote += 1
        if self._ticks_since_quote < self.quote_every_n_ticks:
            return []
        self._ticks_since_quote = 0

        # Offer the energy the plant could generate over one quote window.
        qty_f = cap_kw * ctx.tick_duration_h * self.quote_every_n_ticks
        qty = Decimal(str(qty_f)).quantize(Decimal("0.0001"))
        if qty < self.min_qty:
            return []
        price = Decimal(str(ctx.params.gas_cost_per_kwh)).quantize(Decimal("0.0001"))
        if price <= 0:
            return []
        return [OrderIntent(side="sell", price=price, qty=qty, dispatched=True)]
