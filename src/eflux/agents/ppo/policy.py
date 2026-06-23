"""Wraps a Ray RLlib checkpoint for inference inside the live simulator.

The training script (`eflux.agents.ppo.train`) writes checkpoints via `Algorithm.save()`.
Here we load one and expose a tiny `act(obs) -> action` API.

Uses RLlib's "new API stack" inference path — `compute_single_action` is deprecated
on the new stack, so we go directly through the RLModule's forward_inference.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class PPOPolicyWrapper:
    def __init__(
        self,
        checkpoint_path: str | Path,
        *,
        env_name: str = "eflux_vpp",
        env_factory=None,
    ) -> None:
        self._checkpoint_path = Path(checkpoint_path)
        # The env the checkpoint was trained with must be registered before
        # from_checkpoint() rebuilds its env runners. Defaults to the original
        # single-agent env; the structured-action path passes its own.
        self._env_name = env_name
        self._env_factory = env_factory
        self._algo = None  # lazy — Ray init is expensive
        self._module = None
        self._torch = None  # lazy import

    def _ensure_loaded(self) -> None:
        if self._algo is not None:
            return
        from ray.rllib.algorithms.algorithm import Algorithm
        from ray.tune.registry import register_env

        if not self._checkpoint_path.exists():
            raise FileNotFoundError(f"PPO checkpoint not found: {self._checkpoint_path}")

        # Algorithm.from_checkpoint() rebuilds env runners, which look up the env
        # by name in the Tune registry. The live backend hasn't registered it — do
        # it now so loading succeeds.
        if self._env_factory is not None:
            register_env(self._env_name, self._env_factory)
        else:
            from eflux.agents.ppo.env import VPPSingleAgentEnv

            register_env(self._env_name, lambda config: VPPSingleAgentEnv(config))

        log.info("Loading PPO checkpoint from %s", self._checkpoint_path)
        # PyArrow (used by from_checkpoint) rejects bare relative paths as "URI has
        # empty scheme", so always pass an absolute path.
        self._algo = Algorithm.from_checkpoint(str(self._checkpoint_path.resolve()))
        self._module = self._algo.get_module()  # default policy's RLModule
        import torch
        self._torch = torch

    def act(self, obs: np.ndarray) -> np.ndarray:
        self._ensure_loaded()
        torch = self._torch
        obs_tensor = torch.from_numpy(np.asarray(obs, dtype=np.float32)).unsqueeze(0)
        with torch.no_grad():
            out = self._module.forward_inference({"obs": obs_tensor})
        # PPO with continuous Box action space uses a DiagonalGaussian distribution:
        # action_dist_inputs = concat([mean, log_std]). For deterministic inference
        # we take the mean.
        dist_inputs = out["action_dist_inputs"]
        n = dist_inputs.shape[-1] // 2
        mean = dist_inputs[..., :n]
        action = mean.squeeze(0).cpu().numpy().astype(np.float32)
        return action
