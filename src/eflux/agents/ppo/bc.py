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
import random
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import torch
from torch import nn

from eflux.agents.base import AgentContext
from eflux.agents.hybrid import StrategyAgent
from eflux.agents.ppo.primitive_encoding import (
    ACTION_PROFILE_P2P,
    ENCODING_V2,
    OBS_DIM_V4,
    OBS_V4,
    action_profile_for_action_dim,
    action_profile_for_market,
    decode_action,
    encode_action,
    encode_obs,
    encoding_version_for_action_dim,
    infer_action_dim,
    infer_action_profile,
    infer_encoding_version,
    infer_obs_dim,
    obs_dim_for,
    obs_version_for_obs_dim,
    price_ref_scale,
    primitive_modes_for,
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
# Reserve a stable slice of primitive demonstrations for zero-endowment pure traders.
DEMO_BATTERY_ONLY_FRACTION = 0.225


def _battery_only_demo_params(
    n_episodes: int, seed: int, *, fraction: float = DEMO_BATTERY_ONLY_FRACTION
) -> dict[int, object]:
    """Deterministically assign uniformly sampled battery-only cells to demo episodes."""
    from eflux.vpp.base import VPPParams

    count = round(max(0, n_episodes) * max(0.0, min(1.0, fraction)))
    if count == 0:
        return {}
    rng = random.Random(seed ^ 0xBA77E2)
    episode_indexes = rng.sample(range(n_episodes), count)
    return {
        episode: VPPParams(
            pv_kw_peak=0.0,
            wind_kw_rated=0.0,
            load_kw_base=0.0,
            battery_kwh=rng.uniform(10.0, 30.0),
            battery_kw_max=rng.uniform(3.0, 6.0),
            markup_floor=0.4,
        )
        for episode in episode_indexes
    }


class BCNet(nn.Module):
    """Small MLP mapping an observation to a raw action vector (the same space the PPO
    module acts in, so these weights can warm-start PPO)."""

    def __init__(
        self,
        obs_dim: int = OBS_DIM_V4,
        action_dim: int | None = None,
        hidden: int = 64,
        *,
        encoding_version: int = ENCODING_V2,
        obs_version: int | None = None,
        action_profile: str | None = None,
    ) -> None:
        super().__init__()
        if action_dim is not None:
            self.action_dim = int(action_dim)
            self.action_profile = action_profile or action_profile_for_action_dim(self.action_dim)
        else:
            self.action_profile = action_profile or ACTION_PROFILE_P2P
            self.action_dim = encoding_action_dim(
                encoding_version, action_profile=self.action_profile
            )
        self.encoding_version = encoding_version_for_action_dim(self.action_dim)
        self.obs_dim = int(obs_dim)
        self.obs_version = (
            obs_version if obs_version is not None else obs_version_for_obs_dim(self.obs_dim)
        )
        self.net = nn.Sequential(
            nn.Linear(self.obs_dim, hidden),
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
    encoding_version: int = ENCODING_V2,
    obs_version: int = OBS_V4,
    action_profile: str | None = None,
    battery_only_fraction: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Roll the expert through VPPPrimitiveEnv, recording (obs, encoded-action) pairs.

    Obs and the expert's decision are computed with our own oracle at `demand_beta` (so
    they match the serving config), independent of the env's internal stepping oracle —
    the env only supplies a diverse DER/market state trajectory to clone over. `env_config`
    is passed to the env (e.g. {"real_data": ...} to clone over real price/weather)."""
    from decimal import Decimal

    from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv

    cfg = dict(env_config or {})
    action_profile = (
        action_profile
        or cfg.get("action_profile")
        or action_profile_for_market(str(cfg.get("market_mode", "p2p")))
    )
    cfg.setdefault("encoding_version", encoding_version)
    cfg.setdefault("obs_version", obs_version)
    cfg.setdefault("action_profile", action_profile)
    env = VPPPrimitiveEnv(cfg)
    oracle = TruthfulValuationOracle(
        price_ref=Decimal(str(price_ref_scale())), demand_beta=demand_beta
    )
    obs_rows: list[np.ndarray] = []
    act_rows: list[np.ndarray] = []
    battery_only_params = _battery_only_demo_params(
        n_episodes, seed, fraction=battery_only_fraction
    )
    for ep in range(n_episodes):
        params = battery_only_params.get(ep)
        env.reset(seed=seed + ep, options={"params": params} if params is not None else None)
        for _ in range(env._episode_ticks):
            ctx = env._make_ctx()
            valuation = oracle.estimate(ctx)
            action = expert.select_action(ctx, valuation)
            obs_rows.append(encode_obs(ctx, valuation, obs_version=obs_version))
            encoded = encode_action(action, version=encoding_version, action_profile=action_profile)
            act_rows.append(encoded)
            env.step(encoded)
    return np.asarray(obs_rows, dtype=np.float32), np.asarray(act_rows, dtype=np.float32)


def collect_scenario_demonstrations(
    expert: StrategyPolicy,
    *,
    n_episodes: int = 10,
    intervals_per_episode: int = 288,
    seed: int = 0,
    market_mode: str = "p2p",
    demand_beta: float = _DEMO_DEMAND_BETA,
    encoding_version: int = ENCODING_V2,
    obs_version: int = OBS_V4,
    action_profile: str | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Collect expert decisions inside the multi-agent live simulator topology.

    The primitive env supplies broad price/weather coverage, while these rows
    cover the actual scheduler, counterparties, product books, forecasts, and
    delivery cadence used by serving.  Mixing both prevents a checkpoint that
    scores well in the single-agent env but stands down in the live market.
    """

    from eflux.agents.bench.run import run_episode

    profile = action_profile or action_profile_for_market(market_mode)
    price_ref = Decimal(str(price_ref_scale()))
    obs_rows: list[np.ndarray] = []
    act_rows: list[np.ndarray] = []

    class RecordingPolicy:
        def select_action(self, ctx, valuation, guidance=None):
            action = expert.select_action(ctx, valuation, guidance)
            obs_rows.append(encode_obs(ctx, valuation, obs_version=obs_version))
            act_rows.append(
                encode_action(
                    action,
                    version=encoding_version,
                    action_profile=profile,
                )
            )
            return action

    for episode in range(n_episodes):
        recorder = RecordingPolicy()
        run_episode(
            lambda recorder=recorder: StrategyAgent(
                price_ref=price_ref,
                demand_beta=demand_beta,
                use_forecast=True,
                policy=recorder,
            ),
            n_ticks=intervals_per_episode,
            tick_h=5.0 / 60.0,
            forecasts_enabled=True,
            episode_seed=seed + episode,
            market_price_ref=price_ref,
            market_mode=market_mode,
        )
    return np.asarray(obs_rows, dtype=np.float32), np.asarray(act_rows, dtype=np.float32)


def train_bc(
    obs: np.ndarray,
    acts: np.ndarray,
    *,
    epochs: int = 300,
    lr: float = 1e-3,
    seed: int = 0,
    hidden: int = 64,
    encoding_version: int = ENCODING_V2,
    obs_version: int = OBS_V4,
    action_profile: str | None = None,
) -> BCNet:
    """Clone the expert: cross-entropy on the primitive choice (the mode logits) plus
    MSE on the squashed parameters. A single MSE over the whole vector would let the
    large parameter targets swamp the small mode logits and underfit the mode."""
    torch.manual_seed(seed)
    obs_dim = int(obs.shape[1])
    action_profile = action_profile or action_profile_for_action_dim(int(acts.shape[1]))
    n_modes = len(primitive_modes_for(action_profile=action_profile))
    net = BCNet(
        obs_dim=obs_dim,
        action_dim=acts.shape[1],
        hidden=hidden,
        encoding_version=encoding_version,
        obs_version=obs_version_for_obs_dim(obs_dim),
        action_profile=action_profile,
    )
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    mse = nn.MSELoss()
    x = torch.as_tensor(obs)
    expected_obs_dim = obs_dim_for(obs_version)
    if obs.shape[1] != expected_obs_dim:
        raise ValueError(
            f"expected obs width {expected_obs_dim} for observation V{obs_version}, got {obs.shape[1]}"
        )
    mode_idx = torch.as_tensor(np.argmax(acts[:, :n_modes], axis=1)).long()
    # Delivery data is naturally imbalanced (e.g. nighttime deficit dominates
    # solar surplus).  Unweighted CE can score highly by predicting the majority
    # mode while never learning to sell.  Square-root inverse-frequency weights
    # preserve majority calibration without erasing minority delivery modes.
    counts = torch.bincount(mode_idx, minlength=n_modes).float().clamp_min(1.0)
    class_weights = torch.sqrt(mode_idx.numel() / (n_modes * counts))
    class_weights = class_weights / class_weights.mean()
    ce = nn.CrossEntropyLoss(weight=class_weights)
    params = torch.as_tensor(acts[:, n_modes:])
    net.train()
    for _ in range(epochs):
        opt.zero_grad()
        out = net(x)
        loss = ce(out[:, :n_modes], mode_idx) + mse(out[:, n_modes:], params)
        loss.backward()
        opt.step()
    net.eval()
    return net


def mode_accuracy(net: BCNet, obs: np.ndarray, acts: np.ndarray) -> float:
    """Fraction of samples where the cloned net picks the expert's primitive."""
    n_modes = len(
        primitive_modes_for(
            action_profile=getattr(net, "action_profile", None)
            or action_profile_for_action_dim(acts.shape[1])
        )
    )
    with torch.no_grad():
        pred = net(torch.as_tensor(obs)).numpy()
    return float((pred[:, :n_modes].argmax(1) == acts[:, :n_modes].argmax(1)).mean())


def trade_mode_accuracy(net: BCNet, obs: np.ndarray, acts: np.ndarray) -> float:
    """Mode accuracy on samples where the expert actually trades (non-NOOP). The
    razor-thin NOOP boundary (surplus/deficit just under min_qty) is dust-filtered by
    the compiler downstream, so the trade decisions are what matter for behaviour."""
    n_modes = len(
        primitive_modes_for(
            action_profile=getattr(net, "action_profile", None)
            or action_profile_for_action_dim(acts.shape[1])
        )
    )
    true = np.argmax(acts[:, :n_modes], axis=1)
    mask = true != 0
    if not mask.any():
        return 1.0
    with torch.no_grad():
        pred = net(torch.as_tensor(obs)).numpy()[:, :n_modes].argmax(1)
    return float((pred[mask] == true[mask]).mean())


def per_mode_recall(net: BCNet, obs: np.ndarray, acts: np.ndarray) -> dict[str, float]:
    """Recall for every demonstrated primitive; exposes majority-mode collapse."""

    modes = primitive_modes_for(action_profile=getattr(net, "action_profile", None))
    true = np.argmax(acts[:, : len(modes)], axis=1)
    with torch.no_grad():
        pred = net(torch.as_tensor(obs)).numpy()[:, : len(modes)].argmax(1)
    return {
        mode.value: float((pred[true == idx] == idx).mean())
        for idx, mode in enumerate(modes)
        if np.any(true == idx)
    }


def mean_episode_reward(
    policy: StrategyPolicy,
    *,
    n_episodes: int = 8,
    seed: int = 0,
    env_config: dict | None = None,
    encoding_version: int = ENCODING_V2,
    obs_version: int = OBS_V4,
    action_profile: str | None = None,
) -> float:
    """Mean total VPPPrimitiveEnv reward when `policy` drives it — the warm-start
    metric (how competent a starting point the policy gives PPO, in PPO's own env)."""
    from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv

    cfg = dict(env_config or {})
    action_profile = (
        action_profile
        or cfg.get("action_profile")
        or action_profile_for_market(str(cfg.get("market_mode", "p2p")))
    )
    cfg.setdefault("encoding_version", encoding_version)
    cfg.setdefault("obs_version", obs_version)
    cfg.setdefault("action_profile", action_profile)
    env = VPPPrimitiveEnv(cfg)
    totals: list[float] = []
    for ep in range(n_episodes):
        env.reset(seed=seed + ep)
        total = 0.0
        for _ in range(env._episode_ticks):
            ctx = env._make_ctx()
            valuation = env._oracle.estimate(ctx)
            _o, r, _t, _tr, _ = env.step(
                encode_action(
                    policy.select_action(ctx, valuation),
                    version=encoding_version,
                    action_profile=action_profile,
                )
            )
            total += r
        totals.append(total)
    return float(np.mean(totals))


def mean_random_reward(
    *,
    n_episodes: int = 8,
    seed: int = 0,
    env_config: dict | None = None,
    encoding_version: int = ENCODING_V2,
    obs_version: int = OBS_V4,
    action_profile: str | None = None,
) -> float:
    """Mean total reward of a uniformly-random policy — the warm-start floor."""
    from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv

    cfg = dict(env_config or {})
    action_profile = (
        action_profile
        or cfg.get("action_profile")
        or action_profile_for_market(str(cfg.get("market_mode", "p2p")))
    )
    cfg.setdefault("encoding_version", encoding_version)
    cfg.setdefault("obs_version", obs_version)
    cfg.setdefault("action_profile", action_profile)
    env = VPPPrimitiveEnv(cfg)
    rng = np.random.default_rng(seed)
    totals: list[float] = []
    for ep in range(n_episodes):
        env.reset(seed=seed + ep)
        total = 0.0
        for _ in range(env._episode_ticks):
            _o, r, _t, _tr, _ = env.step(
                rng.uniform(-5.0, 5.0, size=env.action_dim).astype(np.float32)
            )
            total += r
        totals.append(total)
    return float(np.mean(totals))


@dataclass
class BCPolicy:
    """A StrategyPolicy backed by a behavior-cloned network."""

    net: BCNet
    encoding_version: int | None = None
    obs_version: int | None = None
    action_profile: str | None = None

    def __post_init__(self) -> None:
        if self.encoding_version is None:
            self.encoding_version = encoding_version_for_action_dim(self.net.net[-1].out_features)
        if self.action_profile is None:
            self.action_profile = getattr(
                self.net, "action_profile", None
            ) or action_profile_for_action_dim(self.net.net[-1].out_features)
        if self.obs_version is None:
            self.obs_version = obs_version_for_obs_dim(self.net.net[0].in_features)

    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        obs = encode_obs(ctx, valuation, obs_version=self.obs_version)
        with torch.no_grad():
            vec = self.net(torch.as_tensor(obs).unsqueeze(0)).squeeze(0).numpy()
        return decode_action(vec, version=self.encoding_version, action_profile=self.action_profile)


def train_bc_policy(
    *,
    expert: StrategyPolicy | None = None,
    n_episodes: int = 40,
    epochs: int = 300,
    seed: int = 0,
    encoding_version: int = ENCODING_V2,
    obs_version: int = OBS_V4,
    action_profile: str | None = None,
) -> BCPolicy:
    obs, acts = collect_demonstrations(
        expert or BatteryAwareStrategyPolicy(),
        n_episodes=n_episodes,
        seed=seed,
        encoding_version=encoding_version,
        obs_version=obs_version,
        action_profile=action_profile,
    )
    return BCPolicy(
        train_bc(
            obs,
            acts,
            epochs=epochs,
            seed=seed,
            encoding_version=encoding_version,
            obs_version=obs_version,
            action_profile=action_profile,
        ),
        action_profile=action_profile,
    )


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
    obs_version: int | None = None,
    action_profile: str | None = None,
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
            "action_profile": action_profile
            or getattr(net, "action_profile", None)
            or action_profile_for_action_dim(net.net[-1].out_features),
            "encoding_version": encoding_version
            if encoding_version is not None
            else encoding_version_for_action_dim(net.net[-1].out_features),
            "obs_dim": int(net.net[0].in_features),
            "obs_version": obs_version
            if obs_version is not None
            else obs_version_for_obs_dim(net.net[0].in_features),
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
    raw = torch.load(path)
    state = _unwrap_state(raw)
    version = infer_encoding_version(state)
    obs_dim = infer_obs_dim(state)
    action_profile = infer_action_profile(raw if isinstance(raw, dict) else state)
    net = BCNet(
        obs_dim=obs_dim,
        action_dim=infer_action_dim(state),
        hidden=hidden,
        encoding_version=version,
        action_profile=action_profile,
    )
    net.load_state_dict(state)
    net.eval()
    return net
