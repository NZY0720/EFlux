"""Evaluate a trained primitive-PPO checkpoint against the M3 benchmark.

Scores the learned policy in the same fixed scenario the scripted StrategyAgent and
Truthful are scored in, so the leaderboard answers the M4 acceptance question directly:
is the structured action space learnable, and does the learned policy reach (or beat) the
scripted baseline? Run via:
    ./tasks.sh train-ppo --env primitive --iters 200 --out checkpoints/ppo_primitive
    .env/bin/python -m eflux.agents.ppo.eval --checkpoint checkpoints/ppo_primitive
"""

from __future__ import annotations

import argparse

from eflux.agents.bench.metrics import format_leaderboard
from eflux.agents.bench.run import score
from eflux.agents.bench.scenarios import candidates
from eflux.agents.ppo.primitive_agent import build_ppo_primitive_agent


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate a primitive-PPO checkpoint vs the benchmark baselines.")
    ap.add_argument("--checkpoint", required=True, help="path to the trained checkpoint dir")
    ap.add_argument("--ticks", type=int, default=144)
    ap.add_argument("--tick-minutes", type=float, default=10.0)
    args = ap.parse_args()
    tick_h = args.tick_minutes / 60.0

    rows = [score(name, make, n_ticks=args.ticks, tick_h=tick_h) for name, make in candidates().items()]
    rows.append(
        score(
            "ppo-primitive",
            lambda: build_ppo_primitive_agent(args.checkpoint),
            n_ticks=args.ticks,
            tick_h=tick_h,
        )
    )
    print(format_leaderboard(rows))


if __name__ == "__main__":
    main()
