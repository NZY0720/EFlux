"""GD — Gjerstad-Dickhaut (1998) belief-based bidding.

GD keeps a sliding window of recent market activity and, from it, estimates a **belief
function** q(p): the probability that an order at price p would be accepted. It then quotes
the price that maximises expected surplus — `(p - limit)·q(p)` for a sell, `(limit - p)·q(p)`
for a buy — over a grid of candidate prices.

Belief (asks, symmetric for bids):

    q(a) = [ taken_asks(≤a) + bids(≥a) ] / [ taken_asks(≤a) + bids(≥a) + rejected_asks(≥a) ]

i.e. an ask at `a` is more likely to clear the more often asks at or below `a` have traded
and the more standing bids sit at or above `a`, and less likely the more asks at or above
`a` went unfilled.

Adaptation: this market's engine surfaces best-bid / best-ask snapshots and trade prints
rather than the full order flow, so the window is built from those — each tick samples the
current best bid and best ask, and trade prints (public tape + this VPP's own fills via the
`record_trade` hook) are the "taken" events. With an empty history GD falls back to quoting
its limit (truthful), so it warms up gracefully.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from eflux.agents._baseline_common import BaselineAgent
from eflux.agents.base import AgentContext
from eflux.agents.valuation import ValuationSignal


@dataclass
class GDAgent(BaselineAgent):
    history_len: int = 40          # sliding window of market events
    n_candidates: int = 11         # price grid resolution
    price_band: float = 0.3        # how far past the opposing quote to search, as a fraction
    _bids: deque = field(default_factory=lambda: deque(maxlen=40))
    _asks: deque = field(default_factory=lambda: deque(maxlen=40))
    _trades: deque = field(default_factory=lambda: deque(maxlen=40))

    def __post_init__(self) -> None:
        super().__post_init__()
        # Re-size the windows to the configured length (the field default is a literal).
        self._bids = deque(self._bids, maxlen=self.history_len)
        self._asks = deque(self._asks, maxlen=self.history_len)
        self._trades = deque(self._trades, maxlen=self.history_len)

    def record_trade(self, record: dict) -> None:
        try:
            self._trades.append(float(record["price"]))
        except (KeyError, TypeError, ValueError):
            pass

    def _observe(self, ctx: AgentContext) -> None:
        m = ctx.market
        if m.best_bid is not None:
            self._bids.append(float(m.best_bid))
        if m.best_ask is not None:
            self._asks.append(float(m.best_ask))
        for t in m.recent_trades:
            try:
                self._trades.append(float(t["price"]))
            except (KeyError, TypeError, ValueError):
                continue

    def _quote_price(
        self, *, side: str, limit: float, ctx: AgentContext, sig: ValuationSignal
    ) -> float:
        self._observe(ctx)
        if limit <= 0 or not (self._trades or self._bids or self._asks):
            return limit  # cold start → truthful

        ref = self._last_market_price(ctx) or limit
        if side == "sell":
            lo, hi = limit, max(limit, ref) * (1.0 + self.price_band)
        else:
            lo, hi = min(limit, ref) * (1.0 - self.price_band), limit
        lo = max(lo, 0.0001)
        if hi <= lo:
            return limit

        step = (hi - lo) / max(1, self.n_candidates - 1)
        best_p, best_surplus = limit, -1.0
        for i in range(self.n_candidates):
            p = lo + i * step
            surplus = (p - limit) * self._belief(side, p) if side == "sell" else (limit - p) * self._belief(side, p)
            if surplus > best_surplus:
                best_surplus, best_p = surplus, p
        return best_p

    def _belief(self, side: str, p: float) -> float:
        """GD acceptance belief q(p) from the observed window."""
        taken = sum(1 for t in self._trades if (t <= p if side == "sell" else t >= p))
        if side == "sell":
            willing = sum(1 for b in self._bids if b >= p)   # bids that would lift our ask
            rejected = sum(1 for a in self._asks if a >= p)  # asks at/above p left standing
        else:
            willing = sum(1 for a in self._asks if a <= p)   # asks that would fill our bid
            rejected = sum(1 for b in self._bids if b <= p)  # bids at/below p left standing
        denom = taken + willing + rejected
        if denom <= 0:
            return 0.0
        return (taken + willing) / denom
