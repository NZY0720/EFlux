"""Actor-critic network for the custom online PPO learner (Part C).

A small, self-contained PyTorch policy over the *same* structured-action vector the
offline path uses (`primitive_encoding.ACTION_DIM`), so a checkpoint trained offline (or a
behavior-cloned `BCNet`) warm-starts it 1:1. Deliberately RLlib-free: the live learner must
update inside the simulator without Ray's env-runner/training machinery.

Layout mirrors `bc.BCNet`'s trunk (`Linear(18,64)→Tanh→Linear(64,64)→Tanh`) so the cloned
weights map straight across; on top sit an actor-mean head, a learnable diagonal log-std,
and a value head. The policy is a diagonal Gaussian over the raw action vector — exactly
what `decode_action` expects — matching how the RLlib path samples (mean+log_std).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import nn

from eflux.agents.ppo.primitive_encoding import ACTION_DIM, OBS_DIM

# Initial diagonal log-std (std ≈ 0.61). Modest exploration around a warm-started mean so
# live learning departs from the baseline gradually rather than thrashing the book.
LOG_STD_INIT = -0.5
# Clamp the learnable log-std so the Gaussian never collapses (no gradient) or explodes.
LOG_STD_MIN = -2.5
LOG_STD_MAX = 1.0


class ActorCriticNet(nn.Module):
    """Shared-trunk actor-critic. ``forward`` returns (action mean, state value)."""

    def __init__(self, obs_dim: int = OBS_DIM, action_dim: int = ACTION_DIM, hidden: int = 64) -> None:
        super().__init__()
        # Indices 0 and 2 line up with BCNet.net.0 / net.2 for a clean warm-start remap.
        self.trunk = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.actor_mean = nn.Linear(hidden, action_dim)  # warm-starts from BCNet.net.4
        self.value = nn.Linear(hidden, 1)  # critic head — no BC counterpart, random init
        self.log_std = nn.Parameter(torch.full((action_dim,), float(LOG_STD_INIT)))

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.trunk(x)
        return self.actor_mean(h), self.value(h).squeeze(-1)

    def _std(self) -> torch.Tensor:
        return self.log_std.clamp(LOG_STD_MIN, LOG_STD_MAX).exp()

    def distribution(self, x: torch.Tensor) -> tuple[torch.distributions.Normal, torch.Tensor]:
        mean, value = self.forward(x)
        return torch.distributions.Normal(mean, self._std()), value

    @torch.no_grad()
    def act(self, obs: np.ndarray, *, deterministic: bool = False) -> tuple[np.ndarray, float, float]:
        """Serving step: returns (raw action vector, log-prob, value) as plain Python/NumPy.

        ``deterministic`` takes the mean (eval / mirror-eval); otherwise samples for
        exploration. The returned action vector is the un-squashed policy output — the
        caller passes it to ``decode_action`` to get a bounded ``StrategyAction``."""
        x = torch.from_numpy(np.asarray(obs, dtype=np.float32)).unsqueeze(0)
        dist, value = self.distribution(x)
        action = dist.mean if deterministic else dist.sample()
        logp = dist.log_prob(action).sum(-1)
        return (
            action.squeeze(0).cpu().numpy().astype(np.float32),
            float(logp.squeeze(0)),
            float(value.squeeze(0)),
        )

    @torch.no_grad()
    def act_mean(self, obs: np.ndarray) -> np.ndarray:
        """Deterministic action vector only — the parity surface against ``BCPolicy``."""
        x = torch.from_numpy(np.asarray(obs, dtype=np.float32)).unsqueeze(0)
        mean, _ = self.forward(x)
        return mean.squeeze(0).cpu().numpy().astype(np.float32)


# BCNet (bc.py) → ActorCriticNet key remap. BCNet is an nn.Sequential stored under "net.*".
_BC_TO_AC = {
    "trunk.0.weight": "net.0.weight",
    "trunk.0.bias": "net.0.bias",
    "trunk.2.weight": "net.2.weight",
    "trunk.2.bias": "net.2.bias",
    "actor_mean.weight": "net.4.weight",
    "actor_mean.bias": "net.4.bias",
}


def warm_start_from_bcnet(net: ActorCriticNet, bc_state: dict) -> ActorCriticNet:
    """Copy a behavior-cloned `BCNet` state_dict into `net` (trunk + actor head). The
    value head and log-std keep their fresh init — BC has no critic. Shape-mismatched or
    missing keys are skipped so a stale checkpoint degrades gracefully rather than raising."""
    sd = net.state_dict()
    for dst, src in _BC_TO_AC.items():
        tensor = bc_state.get(src)
        if tensor is not None and tuple(tensor.shape) == tuple(sd[dst].shape):
            sd[dst] = tensor.clone()
    net.load_state_dict(sd)
    return net


def _is_bcnet_state(state: dict) -> bool:
    """A BCNet state_dict is the bare Sequential ("net.0.*"); a resumed ActorCriticNet
    carries our own keys ("trunk.*", "actor_mean.*", "value.*", "log_std")."""
    return any(k.startswith("net.") for k in state) and not any(k.startswith("trunk.") for k in state)


def load_warm_start(
    path: str | Path,
    *,
    obs_dim: int = OBS_DIM,
    action_dim: int = ACTION_DIM,
    hidden: int = 64,
    map_location: str = "cpu",
) -> ActorCriticNet:
    """Build an ActorCriticNet from a checkpoint, autodetecting its kind: a BC `BCNet`
    state_dict warm-starts the trunk+actor; a previously-saved ActorCriticNet state_dict
    (live weights persisted on shutdown) is loaded whole to resume learning."""
    state = torch.load(str(path), map_location=map_location)
    net = ActorCriticNet(obs_dim=obs_dim, action_dim=action_dim, hidden=hidden)
    if _is_bcnet_state(state):
        warm_start_from_bcnet(net, state)
    else:
        net.load_state_dict(state)
    net.eval()
    return net
