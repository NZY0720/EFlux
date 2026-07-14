"""Valuation layer.

`TruthfulValuationOracle` estimates what a VPP's energy is economically worth each
tick (fair buy/sell prices, battery opportunity cost, imbalance, SOC pressure) and
returns a `ValuationSignal`. It is the single source of valuation truth: the Truthful
agent, the strategy compiler, and TradingGatewayV1 all read the signal instead of
recomputing the economics. See agents/2026 design note §5.3.
"""

from __future__ import annotations

from eflux.agents.valuation.schema import ValuationSignal
from eflux.agents.valuation.truthful_oracle import TruthfulValuationOracle

__all__ = ["TruthfulValuationOracle", "ValuationSignal"]
