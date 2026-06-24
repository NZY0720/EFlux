"""Rollout buffer + GAE for the online PPO learner (Part C).

Holds *complete* transitions (obs, raw-action, log-prob, value, reward, done) collected on
the live tick path, and computes Generalized Advantage Estimation when a segment closes.

The live sim is continuous (no episode boundaries), so a segment is just a fixed-length
window; ``compute`` bootstraps off the value of the *next* (still-pending) state rather than
treating the cut as terminal — `dones` stays False unless a real terminal ever occurs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch


@dataclass
class RolloutBuffer:
    """Append-only store of complete transitions for one PPO update segment."""

    obs: list[np.ndarray] = field(default_factory=list)
    actions: list[np.ndarray] = field(default_factory=list)
    logprobs: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    rewards: list[float] = field(default_factory=list)
    dones: list[bool] = field(default_factory=list)

    def add(
        self,
        obs: np.ndarray,
        action: np.ndarray,
        logprob: float,
        value: float,
        reward: float,
        done: bool = False,
    ) -> None:
        self.obs.append(np.asarray(obs, dtype=np.float32))
        self.actions.append(np.asarray(action, dtype=np.float32))
        self.logprobs.append(float(logprob))
        self.values.append(float(value))
        self.rewards.append(float(reward))
        self.dones.append(bool(done))

    def __len__(self) -> int:
        return len(self.rewards)

    def clear(self) -> None:
        self.obs.clear()
        self.actions.clear()
        self.logprobs.clear()
        self.values.clear()
        self.rewards.clear()
        self.dones.clear()

    def compute_gae(
        self, *, last_value: float, gamma: float = 0.99, lam: float = 0.95
    ) -> dict[str, torch.Tensor]:
        """GAE(λ) advantages + returns for the stored segment.

        ``last_value`` is V(s_{T}) — the critic's estimate of the first state *after* the
        last stored transition (the still-pending live tick) — used to bootstrap the tail.
        Returns tensors ready for the PPO update; advantages are returned raw (the learner
        normalizes per-minibatch)."""
        n = len(self.rewards)
        rewards = np.asarray(self.rewards, dtype=np.float32)
        values = np.asarray(self.values, dtype=np.float32)
        dones = np.asarray(self.dones, dtype=np.float32)
        adv = np.zeros(n, dtype=np.float32)
        last_adv = 0.0
        for t in range(n - 1, -1, -1):
            next_value = values[t + 1] if t + 1 < n else float(last_value)
            next_nonterminal = 1.0 - dones[t]
            delta = rewards[t] + gamma * next_value * next_nonterminal - values[t]
            last_adv = delta + gamma * lam * next_nonterminal * last_adv
            adv[t] = last_adv
        returns = adv + values
        return {
            "obs": torch.as_tensor(np.asarray(self.obs, dtype=np.float32)),
            "actions": torch.as_tensor(np.asarray(self.actions, dtype=np.float32)),
            "logprobs": torch.as_tensor(np.asarray(self.logprobs, dtype=np.float32)),
            "values": torch.as_tensor(values),
            "advantages": torch.as_tensor(adv),
            "returns": torch.as_tensor(returns),
        }
