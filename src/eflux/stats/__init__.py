"""Durable market results: session identity, per-agent stat snapshots, scoring.

Live market state (orders, trades, PnL) is in-memory by design and wiped on restart.
This package is the durability layer the leaderboard stands on: a market_sessions row
per backend boot, periodic vpp_stat_snapshots samples written off the tick path, and
the endowment-normalized score computed at query time.
"""

from eflux.stats.categories import agent_category
from eflux.stats.score import compute_score

__all__ = ["agent_category", "compute_score"]
