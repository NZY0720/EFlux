"""ZIP — Zero-Intelligence Plus (Cliff 1997).

ZI agents quote a random-but-rational price; ZIP adds a single adaptive variable on top:
a **profit margin** μ ≥ 0 that the agent learns by Widrow-Hoff toward a target derived
from the last market activity. A seller quotes `limit·(1+μ)` (ask above marginal cost); a
buyer quotes `limit·(1-μ)` (bid below marginal value). The margin grows when the market
shows the agent could profit more, and shrinks when the agent is being priced out — the
classic Cliff (1997) "raise if you can, lower if you must" rule.

`limit` (the rationality bound) is the oracle's marginal cost / value, so the margin is
applied on top of the same private value every baseline uses. The market signal is the
last trade print (`ctx.market.last_price` / `recent_trades`), plus this VPP's own fills via
the runner's `record_trade` hook. Adapted to this market's coarse book view: ZIP's original
shout-by-shout update is keyed here on the last *trade* price, which is the acceptance
signal the engine actually surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eflux.agents._baseline_common import BaselineAgent
from eflux.agents.base import AgentContext
from eflux.agents.valuation import ValuationSignal


@dataclass
class ZIPAgent(BaselineAgent):
    # Widrow-Hoff learning rate (β) and momentum (gamma) on the quote price — Cliff's defaults
    # sit around β∈[0.1,0.5], gamma∈[0,0.1].
    beta: float = 0.3
    momentum: float = 0.05
    # Per-update relative/absolute perturbation bands (Cliff's R, A) — small nudges so the
    # target sits just above/below the last print, seeking a little more profit each step.
    rel_perturb: float = 0.05
    abs_perturb: float = 0.5
    init_margin: float = 0.05
    max_margin: float = 0.5
    _margin: float = field(default=0.05)
    _prev_change: float = field(default=0.0)
    _last_fill_price: float | None = field(default=None)

    def __post_init__(self) -> None:
        super().__post_init__()
        self._margin = self.init_margin

    def record_trade(self, record: dict) -> None:
        """Runner hook: remember this VPP's own fill price as a fallback acceptance signal
        when the public tape is quiet."""
        try:
            self._last_fill_price = float(record["price"])
        except (KeyError, TypeError, ValueError):
            pass

    def _quote_price(
        self, *, side: str, limit: float, ctx: AgentContext, sig: ValuationSignal
    ) -> float:
        if limit <= 0:
            return limit
        cur_price = limit * (1.0 + self._margin) if side == "sell" else limit * (1.0 - self._margin)
        q = self._last_market_price(ctx)
        if q is None:
            q = self._last_fill_price
        if q is not None and q > 0:
            self._update_margin(side, cur_price, q, limit, ctx)
            cur_price = (
                limit * (1.0 + self._margin) if side == "sell" else limit * (1.0 - self._margin)
            )
        return cur_price

    def _update_margin(
        self, side: str, cur_price: float, q: float, limit: float, ctx: AgentContext
    ) -> None:
        """Cliff's margin update: pick a target price relative to the last print q (raise
        when the market clears at/above our quote, lower when it clears below us), then take
        a Widrow-Hoff step on the quote price and back out the new margin."""
        r = ctx.rng.uniform(0.0, self.rel_perturb)
        a = ctx.rng.uniform(0.0, self.abs_perturb)
        if side == "sell":
            raise_margin = q >= cur_price  # deals at/above our ask → demand is strong
            target = q * (1.0 + r) + a if raise_margin else q * (1.0 - r) - a
        else:  # buy
            lower_bid = q <= cur_price  # deals at/below our bid → supply is strong
            target = q * (1.0 - r) - a if lower_bid else q * (1.0 + r) + a

        change = self.beta * (target - cur_price) + self.momentum * self._prev_change
        self._prev_change = change
        new_price = cur_price + change
        new_margin = (new_price / limit - 1.0) if side == "sell" else (1.0 - new_price / limit)
        self._margin = max(0.0, min(self.max_margin, new_margin))
