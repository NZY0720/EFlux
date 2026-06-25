"""Evaluate a trained torch checkpoint against the benchmark baselines.

Scores the learned policy in the same fixed scenario the scripted StrategyAgent and
Truthful are scored in, so the leaderboard answers the acceptance question directly:
is the structured action space learnable, and does the learned policy reach (or beat)
the scripted baseline? The checkpoint is a torch state-dict (e.g. checkpoints/bc_primitive.pt)
as produced by `./tasks.sh train-ppo`. Run via:
    ./tasks.sh train-ppo --out checkpoints/bc_primitive.pt
    .env/bin/python -m eflux.agents.ppo.eval --checkpoint checkpoints/bc_primitive.pt
"""

from __future__ import annotations

import argparse
from decimal import Decimal

from eflux.agents.bench.metrics import format_leaderboard
from eflux.agents.bench.run import score
from eflux.agents.bench.scenarios import candidates
from eflux.agents.hybrid import StrategyAgent
from eflux.agents.ppo.online_ppo import build_online_policy

# Match the training/serving valuation config so the obs channels line up.
EVAL_DEMAND_BETA = 0.5


def _make_online_agent(checkpoint: str) -> StrategyAgent:
    """A StrategyAgent driven by the trained torch policy, frozen (no live updates)
    so the evaluation is deterministic — the same machinery the live agent runs."""
    policy = build_online_policy(checkpoint, learning=False, auto_update=False)
    return StrategyAgent(price_ref=Decimal("50.0"), demand_beta=EVAL_DEMAND_BETA, policy=policy)


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate a trained torch checkpoint vs the benchmark baselines.")
    ap.add_argument("--checkpoint", required=True, help="path to the trained checkpoint (.pt state-dict)")
    ap.add_argument("--ticks", type=int, default=144)
    ap.add_argument("--tick-minutes", type=float, default=10.0)
    args = ap.parse_args()
    tick_h = args.tick_minutes / 60.0

    rows = [score(name, make, n_ticks=args.ticks, tick_h=tick_h) for name, make in candidates().items()]
    rows.append(
        score(
            "ppo-online",
            lambda: _make_online_agent(args.checkpoint),
            n_ticks=args.ticks,
            tick_h=tick_h,
        )
    )
    print(format_leaderboard(rows))


if __name__ == "__main__":
    main()
