"""Behavior-cloning warm-start (design note §7, Stage 2).

Map an expert policy (the battery-aware StrategyPolicy demonstrator) into the
structured action space and clone it with supervised learning, so a learned policy
starts near the baseline instead of exploring unsafely from scratch — better sample
efficiency and fewer invalid early actions before PPO fine-tuning.

The cloned `BCPolicy` implements the same `StrategyPolicy` seam as scripted and PPO
policies, so it drops into a `StrategyAgent` unchanged, and its network is the natural
initialization for the PPO module (same obs/action encoding).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import torch
from torch import nn

from eflux.agents.base import AgentContext
from eflux.agents.hybrid import StrategyAgent
from eflux.agents.ppo.primitive_encoding import (
    ENCODING_V1,
    N_MODES,
    OBS_DIM,
    decode_action,
    encode_action,
    encode_obs,
    encoding_version_for_action_dim,
    infer_encoding_version,
    price_ref_scale,
)
from eflux.agents.ppo.primitive_encoding import (
    action_dim as encoding_action_dim,
)
from eflux.agents.strategy.policy import BatteryAwareStrategyPolicy, StrategyPolicy
from eflux.agents.strategy.schema import StrategyAction
from eflux.agents.valuation import TruthfulValuationOracle, ValuationSignal

log = logging.getLogger(__name__)

# Valuation config the demonstrations are collected under — matched to VPPPrimitiveEnv's
# own oracle so the cloned policy warm-starts PPO consistently in the env PPO trains in.
_DEMO_DEMAND_BETA = 0.5


class BCNet(nn.Module):
    """Small MLP mapping an observation to a raw action vector (the same space the PPO
    module acts in, so these weights can warm-start PPO)."""

    def __init__(
        self,
        obs_dim: int = OBS_DIM,
        action_dim: int | None = None,
        hidden: int = 64,
        *,
        encoding_version: int = ENCODING_V1,
    ) -> None:
        super().__init__()
        self.action_dim = int(action_dim) if action_dim is not None else encoding_action_dim(encoding_version)
        self.encoding_version = encoding_version_for_action_dim(self.action_dim)
        self.net = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
            nn.Linear(hidden, self.action_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def collect_demonstrations(
    expert: StrategyPolicy,
    *,
    n_episodes: int = 40,
    seed: int = 0,
    demand_beta: float = _DEMO_DEMAND_BETA,
    env_config: dict | None = None,
    encoding_version: int = ENCODING_V1,
) -> tuple[np.ndarray, np.ndarray]:
    """Roll the expert through VPPPrimitiveEnv, recording (obs, encoded-action) pairs.

    Obs and the expert's decision are computed with our own oracle at `demand_beta` (so
    they match the serving config), independent of the env's internal stepping oracle —
    the env only supplies a diverse DER/market state trajectory to clone over. `env_config`
    is passed to the env (e.g. {"real_data": ...} to clone over real price/weather)."""
    from decimal import Decimal

    from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv

    cfg = dict(env_config or {})
    cfg.setdefault("encoding_version", encoding_version)
    env = VPPPrimitiveEnv(cfg)
    oracle = TruthfulValuationOracle(price_ref=Decimal(str(price_ref_scale())), demand_beta=demand_beta)
    obs_rows: list[np.ndarray] = []
    act_rows: list[np.ndarray] = []
    for ep in range(n_episodes):
        env.reset(seed=seed + ep)
        for _ in range(env._episode_ticks):
            ctx = env._make_ctx()
            valuation = oracle.estimate(ctx)
            action = expert.select_action(ctx, valuation)
            obs_rows.append(encode_obs(ctx, valuation))
            encoded = encode_action(action, version=encoding_version)
            act_rows.append(encoded)
            env.step(encoded)
    return np.asarray(obs_rows, dtype=np.float32), np.asarray(act_rows, dtype=np.float32)


def train_bc(
    obs: np.ndarray,
    acts: np.ndarray,
    *,
    epochs: int = 300,
    lr: float = 1e-3,
    seed: int = 0,
    hidden: int = 64,
    encoding_version: int = ENCODING_V1,
) -> BCNet:
    """Clone the expert: cross-entropy on the primitive choice (the mode logits) plus
    MSE on the squashed parameters. A single MSE over the whole vector would let the
    large parameter targets swamp the small mode logits and underfit the mode."""
    torch.manual_seed(seed)
    net = BCNet(action_dim=acts.shape[1], hidden=hidden, encoding_version=encoding_version)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    ce = nn.CrossEntropyLoss()
    mse = nn.MSELoss()
    x = torch.as_tensor(obs)
    mode_idx = torch.as_tensor(np.argmax(acts[:, :N_MODES], axis=1)).long()
    params = torch.as_tensor(acts[:, N_MODES:])
    net.train()
    for _ in range(epochs):
        opt.zero_grad()
        out = net(x)
        loss = ce(out[:, :N_MODES], mode_idx) + mse(out[:, N_MODES:], params)
        loss.backward()
        opt.step()
    net.eval()
    return net


def mode_accuracy(net: BCNet, obs: np.ndarray, acts: np.ndarray) -> float:
    """Fraction of samples where the cloned net picks the expert's primitive."""
    with torch.no_grad():
        pred = net(torch.as_tensor(obs)).numpy()
    return float((pred[:, :N_MODES].argmax(1) == acts[:, :N_MODES].argmax(1)).mean())


def trade_mode_accuracy(net: BCNet, obs: np.ndarray, acts: np.ndarray) -> float:
    """Mode accuracy on samples where the expert actually trades (non-NOOP). The
    razor-thin NOOP boundary (surplus/deficit just under min_qty) is dust-filtered by
    the compiler downstream, so the trade decisions are what matter for behaviour."""
    true = np.argmax(acts[:, :N_MODES], axis=1)
    mask = true != 0
    if not mask.any():
        return 1.0
    with torch.no_grad():
        pred = net(torch.as_tensor(obs)).numpy()[:, :N_MODES].argmax(1)
    return float((pred[mask] == true[mask]).mean())


def mean_episode_reward(
    policy: StrategyPolicy,
    *,
    n_episodes: int = 8,
    seed: int = 0,
    env_config: dict | None = None,
    encoding_version: int = ENCODING_V1,
) -> float:
    """Mean total VPPPrimitiveEnv reward when `policy` drives it — the warm-start
    metric (how competent a starting point the policy gives PPO, in PPO's own env)."""
    from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv

    cfg = dict(env_config or {})
    cfg.setdefault("encoding_version", encoding_version)
    env = VPPPrimitiveEnv(cfg)
    totals: list[float] = []
    for ep in range(n_episodes):
        env.reset(seed=seed + ep)
        total = 0.0
        for _ in range(env._episode_ticks):
            ctx = env._make_ctx()
            valuation = env._oracle.estimate(ctx)
            _o, r, _t, _tr, _ = env.step(encode_action(policy.select_action(ctx, valuation), version=encoding_version))
            total += r
        totals.append(total)
    return float(np.mean(totals))


def mean_random_reward(
    *,
    n_episodes: int = 8,
    seed: int = 0,
    env_config: dict | None = None,
    encoding_version: int = ENCODING_V1,
) -> float:
    """Mean total reward of a uniformly-random policy — the warm-start floor."""
    from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv

    cfg = dict(env_config or {})
    cfg.setdefault("encoding_version", encoding_version)
    env = VPPPrimitiveEnv(cfg)
    rng = np.random.default_rng(seed)
    totals: list[float] = []
    for ep in range(n_episodes):
        env.reset(seed=seed + ep)
        total = 0.0
        for _ in range(env._episode_ticks):
            _o, r, _t, _tr, _ = env.step(rng.uniform(-5.0, 5.0, size=env.action_dim).astype(np.float32))
            total += r
        totals.append(total)
    return float(np.mean(totals))


@dataclass
class BCPolicy:
    """A StrategyPolicy backed by a behavior-cloned network."""

    net: BCNet
    encoding_version: int | None = None

    def __post_init__(self) -> None:
        if self.encoding_version is None:
            self.encoding_version = encoding_version_for_action_dim(self.net.net[-1].out_features)

    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        obs = encode_obs(ctx, valuation)
        with torch.no_grad():
            vec = self.net(torch.as_tensor(obs).unsqueeze(0)).squeeze(0).numpy()
        return decode_action(vec, version=self.encoding_version)


def train_bc_policy(
    *,
    expert: StrategyPolicy | None = None,
    n_episodes: int = 40,
    epochs: int = 300,
    seed: int = 0,
    encoding_version: int = ENCODING_V1,
) -> BCPolicy:
    obs, acts = collect_demonstrations(
        expert or BatteryAwareStrategyPolicy(), n_episodes=n_episodes, seed=seed, encoding_version=encoding_version
    )
    return BCPolicy(train_bc(obs, acts, epochs=epochs, seed=seed, encoding_version=encoding_version))


def build_bc_agent(policy: BCPolicy, *, price_ref: Decimal = Decimal("50.0")) -> StrategyAgent:
    """A StrategyAgent driven by the cloned policy, configured with the same oracle
    demand_beta the demonstrations were collected under."""
    return StrategyAgent(price_ref=price_ref, demand_beta=_DEMO_DEMAND_BETA, policy=policy)


def save_bc(
    net: BCNet,
    path: str,
    *,
    price_ref: float | None = None,
    market_mode: str | None = None,
    encoding_version: int | None = None,
) -> None:
    """Save a BC checkpoint wrapping the state-dict with metadata: the fixed price scale the
    net was trained under (so serve/eval can restore train/serve parity) and the market mode
    it was trained for (p2p / realprice). The loaders also accept legacy bare state-dicts."""
    torch.save(
        {
            "format": "bc_primitive_v2",
            "state_dict": net.state_dict(),
            "price_ref": float(price_ref) if price_ref is not None else price_ref_scale(),
            "market_mode": market_mode,
            "encoding_version": encoding_version
            if encoding_version is not None
            else encoding_version_for_action_dim(net.net[-1].out_features),
        },
        path,
    )


def _unwrap_state(raw: object) -> dict:
    """Extract the model state-dict from either the v2 metadata-wrapped checkpoint or a legacy
    bare state-dict."""
    if isinstance(raw, dict) and "state_dict" in raw:
        return raw["state_dict"]
    return raw  # type: ignore[return-value]


def checkpoint_meta(path: str) -> dict:
    """Read a checkpoint's metadata ({} for legacy bare state-dicts). Used by eval / repro to
    restore the exact normalization scale the checkpoint trained under."""
    raw = torch.load(path, map_location="cpu")
    if isinstance(raw, dict) and "state_dict" in raw:
        return {k: v for k, v in raw.items() if k != "state_dict"}
    return {}


def load_bc(path: str, *, hidden: int = 64) -> BCNet:
    state = _unwrap_state(torch.load(path))
    version = infer_encoding_version(state)
    net = BCNet(hidden=hidden, encoding_version=version)
    net.load_state_dict(state)
    net.eval()
    return net
