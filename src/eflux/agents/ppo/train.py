"""CLI for training the PPO agent. Run via: ./tasks.sh train-ppo --iters 10 --out checkpoints/ppo_v1

Example:
    ./tasks.sh train-ppo --iters 50 --out checkpoints/ppo_smoke
    EFLUX_PPO_CHECKPOINT=checkpoints/ppo_smoke ./tasks.sh start    # use it in the live simulator

This module is intentionally minimal — the goal is "interface vetted + a usable
checkpoint exists". Training to convergence (or self-play) is future work.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description="Train PPO on the EFlux single-agent VPP env.")
    p.add_argument("--iters", type=int, default=10, help="number of training iterations")
    p.add_argument("--out", type=Path, default=Path("checkpoints/ppo_v1"), help="checkpoint output directory")
    p.add_argument("--num-env-runners", type=int, default=0, help="rollout workers (0 = local only)")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    log = logging.getLogger("eflux.ppo.train")

    try:
        import ray
        from ray.rllib.algorithms.ppo import PPOConfig
        from ray.tune.registry import register_env
    except ImportError as e:
        print(f"PPO training requires the 'ai' extras (uv sync --extra ai): {e}", file=sys.stderr)
        return 1

    from eflux.agents.ppo.env import VPPSingleAgentEnv

    env_name = "eflux_vpp"
    register_env(env_name, lambda config: VPPSingleAgentEnv(config))

    ray.init(ignore_reinit_error=True, num_cpus=max(2, args.num_env_runners + 1))

    config = (
        PPOConfig()
        .environment(env=env_name, env_config={})
        .env_runners(num_env_runners=args.num_env_runners, rollout_fragment_length="auto")
        .training(train_batch_size=512, num_epochs=4, lr=3e-4)
        .resources(num_gpus=0)
        .framework("torch")
    )

    algo = config.build()
    args.out.mkdir(parents=True, exist_ok=True)
    log.info("Training PPO for %d iters → %s", args.iters, args.out)
    for it in range(args.iters):
        result = algo.train()
        # RLlib's metric layout varies across releases; pull whatever's there.
        reward = (
            result.get("env_runners", {}).get("episode_return_mean")
            or result.get("episode_reward_mean")
            or float("nan")
        )
        log.info("iter %d/%d  episode_reward_mean=%.3f", it + 1, args.iters, reward)

    # Ray 2.5x's save() requires an absolute path (or full URI) — PyArrow rejects
    # bare relative paths as "URI has empty scheme".
    out_abs = args.out.resolve()
    ck = algo.save(str(out_abs))
    log.info("Saved checkpoint to %s", ck)
    algo.stop()
    ray.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
