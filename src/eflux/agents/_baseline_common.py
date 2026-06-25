"""Shared scaffolding for the classical continuous-double-auction baselines.

ZIP, GD, and AA all share the same energy economics: they reuse
`TruthfulValuationOracle` for their private value (the reservation / limit price) and
the surplus/deficit side+qty logic, then layer their own *bidding intelligence* on top.
Isolating that shared layer here means the baselines differ only in how they price a
quote relative to their limit and the observed market — which is exactly the variable
we want to compare against ZI / Truthful / PPO.

Side convention mirrors ZI / Truthful: positive accumulated balance ⇒ sell at or above
marginal cost; negative ⇒ buy at or below marginal value; balanced ⇒ no order. Individual
rationality is enforced centrally (a seller never prices below its marginal cost, a buyer
never above its marginal value), so a subclass's learning rule can never quote a losing
trade no matter how it adapts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import AgentContext, BaseAgent, OrderIntent
from eflux.agents.valuation import TruthfulValuationOracle, ValuationSignal

_QUANT = Decimal("0.0001")


@dataclass
class BaselineAgent(BaseAgent):
    """Common base for the classical CDA baselines.

    Subclasses implement `_quote_price(...)` returning the price to quote for this tick;
    the base resolves side/qty from the oracle's accumulated balance, clamps the price to
    individual rationality + positivity, and assembles the `OrderIntent`.

    `price_ref` is the per-agent marginal-cost basis (jittered per VPP for cost
    diversification, and re-based to the trailing-month CAISO mean — see
    `simulator/scenarios.py::_diversify_cost`). `demand_beta` / `price_cap_mult` shape the
    oracle's scarcity bid exactly as for the Truthful agent.
    """

    price_ref: Decimal = Decimal("50.0")
    demand_beta: float = 0.0
    price_cap_mult: float = 1.5
    min_qty: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        self._oracle = TruthfulValuationOracle(
            price_ref=self.price_ref,
            demand_beta=self.demand_beta,
            price_cap_mult=self.price_cap_mult,
        )

    # -- the template the subclasses fill in ------------------------------------------
    def _quote_price(
        self, *, side: str, limit: float, ctx: AgentContext, sig: ValuationSignal
    ) -> float:
        """Price to quote, given the rationality limit (marginal cost on a sell / marginal
        value on a buy) and the current context. Subclasses override; the base clamps the
        result to rationality so a learner can never quote a losing trade."""
        raise NotImplementedError

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        sig = self._oracle.estimate(ctx)
        min_qty_f = float(self.min_qty)
        if sig.surplus_kwh >= min_qty_f:
            side, qty_f, limit = "sell", sig.surplus_kwh, sig.fair_sell_price
        elif sig.deficit_kwh >= min_qty_f:
            side, qty_f, limit = "buy", sig.deficit_kwh, sig.fair_buy_price
        else:
            return []
        price_f = self._quote_price(side=side, limit=limit, ctx=ctx, sig=sig)
        return self._order(side, self._rationalize(side, price_f, limit), qty_f)

    # -- shared helpers ---------------------------------------------------------------
    @staticmethod
    def _rationalize(side: str, price_f: float, limit: float) -> float:
        """Individual rationality: a seller never asks below its marginal cost; a buyer
        never bids above its marginal value."""
        return max(price_f, limit) if side == "sell" else min(price_f, limit)

    def _order(self, side: str, price_f: float, qty_f: float) -> list[OrderIntent]:
        price = Decimal(str(max(price_f, 0.0001))).quantize(_QUANT)
        qty = Decimal(str(qty_f)).quantize(_QUANT)
        if price <= 0 or qty < self.min_qty:
            return []
        return [OrderIntent(side=side, price=price, qty=qty)]

    @staticmethod
    def _last_market_price(ctx: AgentContext) -> float | None:
        """Most recent observable market price: the last trade print, else the most recent
        market-wide fill, else the mid. None when the book has never printed."""
        m = ctx.market
        if m.last_price is not None:
            return float(m.last_price)
        if m.recent_trades:
            return float(m.recent_trades[0]["price"])
        if m.mid_price is not None:
            return float(m.mid_price)
        return None
