"""CLI + library entry point for training the live PPO policy. Run via: ./tasks.sh train-ppo

Produces a torch warm-start checkpoint (default checkpoints/bc_primitive.pt) via behavior
cloning over the structured StrategyAction space. The live `ppo_online` agents load this
checkpoint and fine-tune it online during the simulation, so it is the starting point every
standalone PPO, PPO mirror, and hybrid executor warm-starts from.

With --real-data, training clones over ~1 month of REAL CAISO price + Open-Meteo weather
(see training_data) instead of the synthetic env, so the policy learns against the real
price curve. The same `run_training()` powers the "renew PPOs" button (it runs in a
background thread and the simulator hot-reloads the result).

Example:
    ./tasks.sh train-ppo --real-data --days 30 --out checkpoints/bc_primitive.pt
    .env/bin/python -m eflux.agents.ppo.eval --checkpoint checkpoints/bc_primitive.pt
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger("eflux.ppo.train")


def run_training(
    out_path: str,
    *,
    real_data: bool = False,
    days: int = 30,
    episodes: int = 40,
    epochs: int = 300,
    seed: int = 0,
    market_mode: str = "p2p",
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """Train a BC warm-start checkpoint and save it to `out_path`. Returns a metrics dict.
    Importable so the renew endpoint can run it in a background thread.

    `market_mode` ("p2p"/"realprice") selects the training env's market structure (peer book
    vs grid price-taker) so each live market gets a checkpoint trained against its own
    dynamics. With real data the fixed normalization scale is set to the trailing-month CAISO
    mean and stamped into the checkpoint, so serve-time restores the exact scale (train/serve
    parity) — and the scale stays a constant, never the live tick."""
    from eflux.agents.ppo.bc import (
        BCPolicy,
        collect_demonstrations,
        mean_episode_reward,
        mean_random_reward,
        mode_accuracy,
        save_bc,
        trade_mode_accuracy,
        train_bc,
    )
    from eflux.agents.ppo.primitive_encoding import price_ref_scale, set_price_ref_scale
    from eflux.agents.strategy.policy import ScriptedStrategyPolicy

    env_config: dict = {"market_mode": market_mode}
    data_window = None
    if real_data:
        from eflux.agents.ppo.training_data import load_real_market_data

        log.info("Loading %d days of real CAISO price + weather…", days)
        data = load_real_market_data(days=days, start_date=start_date, end_date=end_date)
        env_config["real_data"] = data
        # Fix the normalization scale to the trailing-month CAISO mean for this run; the env
        # oracle/encoding and the saved checkpoint all use it (synthetic runs keep 50).
        if start_date is not None and end_date is not None:
            set_price_ref_scale(float(data.price.mean()) if len(data.price) else 50.0)
        else:
            from eflux.data.caiso_reference import caiso_reference_price

            set_price_ref_scale(caiso_reference_price(days=days))
        data_window = {
            "start": data.start.isoformat(),
            "end": data.end.isoformat(),
            "price_points": len(data.price),
        }
    log.info("PPO training: market_mode=%s, price_ref_scale=%.2f", market_mode, price_ref_scale())

    log.info("Collecting demonstrations (%d episodes, seed=%d, real_data=%s)…", episodes, seed, real_data)
    obs, acts = collect_demonstrations(
        ScriptedStrategyPolicy(), n_episodes=episodes, seed=seed, env_config=env_config
    )

    log.info("Behavior-cloning for %d epochs on %d samples…", epochs, len(obs))
    net = train_bc(obs, acts, epochs=epochs, seed=seed)

    metrics = {
        "samples": len(obs),
        "mode_accuracy": round(mode_accuracy(net, obs, acts), 4),
        "trade_mode_accuracy": round(trade_mode_accuracy(net, obs, acts), 4),
        "cloned_reward": round(mean_episode_reward(BCPolicy(net), seed=seed, env_config=env_config), 3),
        "random_reward": round(mean_random_reward(seed=seed, env_config=env_config), 3),
        "real_data": real_data,
        "days": days if real_data else None,
        "data_window": data_window,
        "market_mode": market_mode,
        "price_ref_scale": round(price_ref_scale(), 4),
        "out": out_path,
    }
    log.info("BC mode accuracy: %.3f (trade-only %.3f)", metrics["mode_accuracy"], metrics["trade_mode_accuracy"])
    log.info("Warm-start reward: cloned=%.2f vs random=%.2f", metrics["cloned_reward"], metrics["random_reward"])

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_bc(net, str(out), market_mode=market_mode)
    log.info("Saved checkpoint to %s", out)
    return metrics


def main() -> int:
    p = argparse.ArgumentParser(description="Train the live torch PPO policy (behavior-cloning warm-start).")
    p.add_argument("--real-data", action="store_true", help="clone over ~1 month of real CAISO price + weather")
    p.add_argument("--days", type=int, default=30, help="days of real data to fetch (with --real-data)")
    p.add_argument("--episodes", type=int, default=40, help="demonstration episodes")
    p.add_argument("--epochs", type=int, default=300, help="behavior-cloning epochs")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--market-mode",
        choices=("p2p", "realprice"),
        default="p2p",
        help="train the env against this market's structure (peer book vs grid price-taker)",
    )
    p.add_argument("--out", type=Path, default=Path("checkpoints/bc_primitive.pt"), help="checkpoint output (.pt)")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

    try:
        run_training(
            str(args.out),
            real_data=args.real_data,
            days=args.days,
            episodes=args.episodes,
            epochs=args.epochs,
            seed=args.seed,
            market_mode=args.market_mode,
        )
    except ImportError as e:
        print(f"PPO training requires the 'ai' (+ 'data' for --real-data) extras: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
