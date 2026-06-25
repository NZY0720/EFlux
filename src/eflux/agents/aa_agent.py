"""AA — Adaptive Aggressiveness (Vytelingum, Cliff & Jennings 2008).

AA tracks an estimate of the competitive equilibrium price `p*` from recent trades, and an
**aggressiveness** `r ∈ [-1, 1]` that says how hard to push toward its own limit price:

- A *passive* trader (`r < 0`) quotes cautiously around `p*` (asks above / bids below it),
  trading off fill probability for a better price.
- An *aggressive* trader (`r > 0`) moves its quote toward its limit (a seller down toward
  marginal cost, a buyer up toward marginal value) to win the trade.

`r` is itself learned by Widrow-Hoff: when the market clears on terms *better* than `p*`
for us we relax (less aggressive); when it clears on *worse* terms we push harder. `p*` is
an EWMA over the public tape and this VPP's own fills (`record_trade`).

Adaptation: the original AA uses short- and long-term aggressiveness models with a convex
target function θ. This keeps the same two ideas — equilibrium tracking + aggressiveness
learning — with a single Widrow-Hoff aggressiveness update and a linear target between `p*`
and the limit, which fits this market's coarse book view. Rationality is enforced by the
base class, so the quote never crosses the agent's marginal cost / value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eflux.agents._baseline_common import BaselineAgent
from eflux.agents.base import AgentContext
from eflux.agents.valuation import ValuationSignal


@dataclass
class AAAgent(BaselineAgent):
    pstar_alpha: float = 0.2       # EWMA weight on new trades for the equilibrium estimate
    learn_rate: float = 0.1        # Widrow-Hoff rate on the aggressiveness r
    passive_spread: float = 0.1    # max passive offset from p*, as a fraction (at r = -1)
    _pstar: float | None = field(default=None)
    _r: float = field(default=0.0)
    _last_fill_price: float | None = field(default=None)

    def record_trade(self, record: dict) -> None:
        try:
            self._last_fill_price = float(record["price"])
            self._update_pstar(self._last_fill_price)
        except (KeyError, TypeError, ValueError):
            pass

    def _update_pstar(self, price: float) -> None:
        if price <= 0:
            return
        self._pstar = price if self._pstar is None else (
            (1.0 - self.pstar_alpha) * self._pstar + self.pstar_alpha * price
        )

    def _quote_price(
        self, *, side: str, limit: float, ctx: AgentContext, sig: ValuationSignal
    ) -> float:
        # 1) Update the equilibrium estimate from the public tape.
        for t in ctx.market.recent_trades:
            try:
                self._update_pstar(float(t["price"]))
            except (KeyError, TypeError, ValueError):
                continue
        q = self._last_market_price(ctx)
        if q is not None:
            self._update_pstar(q)
        pstar = self._pstar if self._pstar is not None else (q if q is not None else limit)

        # 2) Learn aggressiveness from how the market cleared relative to p*.
        self._update_aggressiveness(side, limit, pstar, q)

        # 3) Target price: passive end hovers around p*; aggressive end moves to the limit.
        r = max(-1.0, min(1.0, self._r))
        if r >= 0.0:
            target = pstar + (limit - pstar) * r          # toward our own limit
        else:
            offset = self.passive_spread * (-r) * pstar   # away from p*, on the safe side
            target = pstar + offset if side == "sell" else pstar - offset
        return target

    def _update_aggressiveness(
        self, side: str, limit: float, pstar: float, q: float | None
    ) -> None:
        """Widrow-Hoff on r toward a target aggressiveness implied by the last print: if the
        market cleared on terms worse than p* for us, push harder; if better, relax."""
        if q is None or pstar <= 0:
            return
        if side == "sell":
            # Selling: a low clearing price is bad for us → be more aggressive (win sooner).
            target_r = 1.0 if q < pstar else -1.0
        else:
            # Buying: a high clearing price is bad for us → be more aggressive.
            target_r = 1.0 if q > pstar else -1.0
        self._r += self.learn_rate * (target_r - self._r)
        self._r = max(-1.0, min(1.0, self._r))
