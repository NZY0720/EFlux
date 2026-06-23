"""Agent benchmark harness.

Fixed-scenario evaluation (design note §9 step 6): each candidate agent is dropped into
the same slot against an identical, deterministic counter-roster and scored on PnL,
mark-to-market, energy traded, residual imbalance, and invalid actions. This is the gate
every later milestone (PPO, BC, hybrid) is measured against — and the proof that the
scripted StrategyAgent is competitive with the Truthful baseline.
"""

from __future__ import annotations

from eflux.agents.bench.metrics import EpisodeMetrics, format_leaderboard

# NB: do not import bench.run here — `python -m eflux.agents.bench.run` imports this
# package first, and re-importing the __main__ module would trigger a RuntimeWarning.

__all__ = ["EpisodeMetrics", "format_leaderboard"]
