"""Online (live) PPO learner + policy over the structured StrategyAction space (Part C).

`OnlineLearner` owns the actor-critic net, a rollout buffer, and a clipped-PPO update.
`OnlinePPOPolicy` implements the `StrategyPolicy` seam: on each tick it encodes the obs,
finalizes the *previous* step's reward from `AgentContext` deltas (the live sim gives no
within-tick boundary, so reward attribution is delayed one tick), samples an action, and —
when learning — buffers the transition and updates on a fixed-length segment cadence.

The learner is RLlib-free and intentionally tiny (~7k params) so a synchronous inline update
is sub-millisecond; the hybrid agent may instead drive updates off the tick path (Part C-M7).
The LLM meta-controller steers it via `apply_meta` (reward-weight scaling, lr/entropy/KL, and
a pull-toward-preferred-modes loss) — all clamped upstream in `MetaControl`.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field

import numpy as np
import torch

from eflux.agents.base import AgentContext
from eflux.agents.ppo.online_net import ActorCriticNet
from eflux.agents.ppo.primitive_encoding import (
    action_profile_for_action_dim,
    decode_action,
    encode_obs,
    encoding_version_for_action_dim,
    infer_action_dim,
    infer_action_profile,
    infer_encoding_version,
    infer_obs_dim,
    primitive_modes_for,
    obs_version_for_obs_dim,
    price_ref_scale,
)
from eflux.agents.ppo.primitive_encoding import (
    action_dim as encoding_action_dim,
)
from eflux.agents.strategy.schema import StrategyAction, StrategyMode
from eflux.agents.valuation import ValuationSignal

log = logging.getLogger(__name__)


# ---- reward (mirrors VPPPrimitiveEnv §7 weights; W_EXCESS_ORDERS dropped — every PPO
# ---- primitive emits a single order, so that term is structurally ~0 online) -----------
@dataclass(frozen=True)
class RewardWeights:
    inventory: float = 0.1   # mark-to-market value of unsettled energy (W_INVENTORY)
    imbalance: float = 1.0   # unserved position (W_IMBALANCE)
    soc: float = 5.0         # asymmetric deviation outside the SOC band (live LLM lever)
    degrade: float = 0.3     # battery throughput this step (W_DEGRADE)
    invalid: float = 10.0    # per gate-vetoed order (W_INVALID)
    soc_low: float = 0.1
    soc_high: float = 0.95
    # Opt-in shaping toward the (LLM-set) soc_target — couples the M4 battery-drain gap to
    # guidance. 0 = off (default), so behaviour matches the offline env unless enabled.
    soc_target_weight: float = 0.0


@dataclass(frozen=True)
class _Snap:
    """The slice of agent state the step reward is computed from, captured at decision time."""

    pnl: float
    pending: float          # pending_net_kwh
    open_net: float         # open_orders_net_kwh
    soc_frac: float
    soc_kwh: float
    rejections: float       # cumulative risk rejections (0 until Part C-M5 surfaces it)
    soc_target: float = 0.5


def _snap(ctx: AgentContext, soc_target: float) -> _Snap:
    return _Snap(
        pnl=float(ctx.state.pnl),
        pending=float(ctx.state.pending_net_kwh),
        open_net=float(ctx.open_orders_net_kwh),
        soc_frac=float(ctx.battery.soc_frac),
        soc_kwh=float(ctx.battery.soc_kwh),
        rejections=float(getattr(ctx, "risk_rejections_total", 0.0) or 0.0),
        soc_target=float(soc_target),
    )


def compute_step_reward(prev: _Snap, cur: _Snap, w: RewardWeights) -> float:
    """Reward for the action taken at the *prev* tick, read off the deltas to the *cur*
    tick. Pure function of two snapshots + weights, so it is unit-testable in isolation."""
    realized = cur.pnl - prev.pnl
    inv_delta = ((cur.pending + cur.soc_kwh) - (prev.pending + prev.soc_kwh)) * price_ref_scale()
    imbalance = abs(cur.pending + cur.open_net)
    soc_dev = max(0.0, w.soc_low - cur.soc_frac) + 0.25 * max(0.0, cur.soc_frac - w.soc_high)
    degrade = abs(cur.soc_kwh - prev.soc_kwh)
    n_rejected = max(0.0, cur.rejections - prev.rejections)
    reward = (
        realized
        + w.inventory * inv_delta
        - w.imbalance * imbalance
        - w.soc * soc_dev
        - w.degrade * degrade
        - w.invalid * n_rejected
    )
    if w.soc_target_weight > 0.0:
        reward -= w.soc_target_weight * (cur.soc_frac - cur.soc_target) ** 2
    return float(reward)


@dataclass
class _Pending:
    obs: np.ndarray
    action: np.ndarray
    logp: float
    value: float
    snap: _Snap


# ---- learner ---------------------------------------------------------------------------
@dataclass
class OnlineLearner:
    """Clipped-PPO learner over a shared-trunk actor-critic. Trains on a *clone* of the
    live net and atomically swaps it in, so the on-tick `net` reference an actor reads is
    never mutated mid-inference (the basis for the off-tick update in Part C-M7)."""

    net: ActorCriticNet = field(default_factory=ActorCriticNet)
    lr: float = 3e-4
    gamma: float = 0.99
    lam: float = 0.95
    clip_eps: float = 0.2
    epochs: int = 4
    minibatch: int = 64
    value_coef: float = 0.5
    entropy_coef: float = 0.01
    max_grad_norm: float = 0.5
    kl_target: float = 0.02   # early-stop the epoch loop past 1.5x; 0 disables
    update_every: int = 64    # segment length (transitions) before an update fires
    min_update_size: int = 16
    mode_reg_coef: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        from eflux.agents.ppo.online_buffer import RolloutBuffer

        self.buffer = RolloutBuffer()
        self._rng = np.random.default_rng(self.seed)
        self._mode_target: torch.Tensor | None = None
        self.action_profile = getattr(self.net, "action_profile", None) or action_profile_for_action_dim(
            self.net.actor_mean.out_features
        )
        self.primitive_modes = primitive_modes_for(action_profile=self.action_profile)
        self.n_modes = len(self.primitive_modes)
        self.update_count = 0

    # -- meta-control (LLM) --------------------------------------------------------------
    def apply_meta(self, meta: object | None) -> None:
        """Adopt a (clamped) `MetaControl`: learning hyperparams + the mode-reg coefficient.
        Reward-weight multipliers live on the policy (reward is computed there). Duck-typed
        and fully optional — a None/partial meta leaves the baseline untouched."""
        if meta is None:
            return
        self.lr = float(getattr(meta, "lr", self.lr))
        self.entropy_coef = float(getattr(meta, "entropy_coef", self.entropy_coef))
        self.kl_target = float(getattr(meta, "kl_target", self.kl_target))
        self.mode_reg_coef = float(getattr(meta, "mode_reg_coef", self.mode_reg_coef))

    def set_mode_target(
        self,
        preferred: tuple[StrategyMode, ...] = (),
        avoid: tuple[StrategyMode, ...] = (),
        *,
        boost: float = 3.0,
        damp: float = 0.25,
    ) -> None:
        """Build the target mode distribution `q` over this checkpoint's PPO primitives from the LLM's
        preferred/avoid sets (modes outside the PPO set are ignored). Cleared when both are
        empty so the reg term goes inert."""
        if not preferred and not avoid:
            self._mode_target = None
            return
        weights = np.ones(self.n_modes, dtype=np.float32)
        for i, mode in enumerate(self.primitive_modes):
            if mode in preferred:
                weights[i] *= boost
            if mode in avoid:
                weights[i] *= damp
        weights = np.clip(weights, 1e-6, None)
        self._mode_target = torch.as_tensor(weights / weights.sum())

    def _mode_reg_loss(self, means: torch.Tensor) -> torch.Tensor:
        """mode_reg_coef · mean KL(q ‖ softmax(actor_mean[:n_modes])). Distinct from the
        execution-time `apply_guidance` — this shapes what the policy *learns*."""
        if self.mode_reg_coef <= 0.0 or self._mode_target is None:
            return means.new_zeros(())
        logp = torch.log_softmax(means[:, :self.n_modes], dim=-1)
        q = self._mode_target
        kl = (q * (q.clamp_min(1e-8).log() - logp)).sum(-1).mean()
        return self.mode_reg_coef * kl

    # -- update --------------------------------------------------------------------------
    def ready(self) -> bool:
        return len(self.buffer) >= self.min_update_size

    def prepare_batch(self, *, last_value: float) -> dict | None:
        """Tick-thread half of an update: run GAE over the segment and clear the buffer,
        returning a self-contained batch. Splitting this from `optimize` lets the heavy
        gradient work run off the tick path (a worker thread) without racing the buffer —
        the only shared mutable state is touched here, synchronously."""
        if len(self.buffer) < self.min_update_size:
            return None
        data = self.buffer.compute_gae(last_value=last_value, gamma=self.gamma, lam=self.lam)
        self.buffer.clear()
        return data

    def update(self, *, last_value: float) -> dict | None:
        """Synchronous convenience: prepare + optimize in one call (auto-update / tests)."""
        data = self.prepare_batch(last_value=last_value)
        if data is None:
            return None
        return self.optimize(data)

    def optimize(self, data: dict) -> dict:
        """Pure compute half: train a *clone* on the prepared batch and atomically swap it
        in. Safe to run on a worker thread — it never touches the buffer, and the only
        shared write is the final `self.net =` reference swap an on-tick `act` reads."""
        obs, actions = data["obs"], data["actions"]
        old_logp, returns, adv = data["logprobs"], data["returns"], data["advantages"]
        n = obs.shape[0]
        if actions.shape[1] != self.net.action_dim:
            raise ValueError(f"PPO batch action width {actions.shape[1]} != net action_dim {self.net.action_dim}")

        work = copy.deepcopy(self.net)
        work.train()
        opt = torch.optim.Adam(work.parameters(), lr=self.lr)
        last_kl = 0.0
        last_loss = 0.0
        for _ in range(self.epochs):
            perm = self._rng.permutation(n)
            for start in range(0, n, self.minibatch):
                mb = perm[start : start + self.minibatch]
                idx = torch.as_tensor(mb, dtype=torch.long)
                dist, value = work.distribution(obs[idx])
                logp = dist.log_prob(actions[idx]).sum(-1)
                entropy = dist.entropy().sum(-1).mean()

                a = adv[idx]
                a = (a - a.mean()) / (a.std() + 1e-8)
                logratio = logp - old_logp[idx]
                ratio = logratio.exp()
                surr1 = ratio * a
                surr2 = ratio.clamp(1.0 - self.clip_eps, 1.0 + self.clip_eps) * a
                policy_loss = -torch.min(surr1, surr2).mean()
                value_loss = (value - returns[idx]).pow(2).mean()
                mode_reg = self._mode_reg_loss(dist.mean)
                loss = (
                    policy_loss
                    + self.value_coef * value_loss
                    - self.entropy_coef * entropy
                    + mode_reg
                )
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(work.parameters(), self.max_grad_norm)
                opt.step()
                last_loss = float(loss.detach())
                # Schulman's positive approx-KL estimator for the early-stop guard.
                last_kl = float(((ratio - 1.0) - logratio).mean().detach())
            if self.kl_target > 0.0 and last_kl > 1.5 * self.kl_target:
                break

        work.eval()
        self.net = work  # atomic reference swap — on-tick `act` sees old-or-new, never torn
        self.update_count += 1
        return {"n": n, "loss": last_loss, "approx_kl": last_kl, "updates": self.update_count}


# ---- policy ----------------------------------------------------------------------------
@dataclass
class OnlinePPOPolicy:
    """`StrategyPolicy` backed by a live-updating `OnlineLearner`.

    `learning=False` serves the deterministic mean and never buffers/updates (frozen eval /
    mirror-eval). `auto_update=True` runs the PPO step synchronously inline when a segment
    fills — sub-ms for this net; set False to let an external scheduler (the hybrid agent)
    drive updates off the tick path."""

    learner: OnlineLearner = field(default_factory=OnlineLearner)
    weights: RewardWeights = field(default_factory=RewardWeights)
    learning: bool = True
    auto_update: bool = True
    soc_target: float = 0.5  # adopted from guidance for the soc_target shaping term

    def __post_init__(self) -> None:
        self._prev: _Pending | None = None
        self._base_weights = self.weights
        self.encoding_version = encoding_version_for_action_dim(self.learner.net.actor_mean.out_features)
        self.action_profile = getattr(self.learner.net, "action_profile", None) or action_profile_for_action_dim(
            self.learner.net.actor_mean.out_features
        )
        self.obs_version = obs_version_for_obs_dim(self.learner.net.trunk[0].in_features)

    # -- meta-control push (off-tick, from the hybrid agent) -----------------------------
    def apply_meta(self, meta: object | None) -> None:
        """Scale reward weights from a (clamped) `MetaControl` and forward learner-side
        levers. Resets to base when meta is None so the learner can't drift on stale steer."""
        if meta is None:
            self.weights = self._base_weights
            self.learner.apply_meta(None)
            return
        b = self._base_weights
        self.weights = RewardWeights(
            inventory=b.inventory,
            imbalance=b.imbalance * float(getattr(meta, "w_imbalance_mult", 1.0)),
            soc=b.soc * float(getattr(meta, "w_soc_mult", 1.0)),
            degrade=b.degrade * float(getattr(meta, "w_degrade_mult", 1.0)),
            invalid=b.invalid,
            soc_low=b.soc_low,
            soc_high=b.soc_high,
            soc_target_weight=b.soc_target_weight,
        )
        self.learner.apply_meta(meta)

    def set_guidance_modes(
        self, preferred: tuple[StrategyMode, ...] = (), avoid: tuple[StrategyMode, ...] = ()
    ) -> None:
        self.learner.set_mode_target(preferred, avoid)

    # -- the seam ------------------------------------------------------------------------
    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        if guidance is not None:
            self.soc_target = float(getattr(guidance, "soc_target", self.soc_target))

        obs = encode_obs(ctx, valuation, obs_version=self.obs_version)

        if not self.learning:
            return decode_action(
                self.learner.net.act_mean(obs),
                version=self.encoding_version,
                action_profile=self.action_profile,
            )

        # Finalize the previous step's reward now that we can see its outcome (one-tick
        # delay), then buffer that complete transition.
        cur = _snap(ctx, self.soc_target)
        if self._prev is not None:
            reward = compute_step_reward(self._prev.snap, cur, self.weights)
            self.learner.buffer.add(
                self._prev.obs, self._prev.action, self._prev.logp, self._prev.value, reward
            )

        action_vec, logp, value = self.learner.net.act(obs, deterministic=False)
        self._prev = _Pending(obs=obs, action=action_vec, logp=logp, value=value, snap=cur)

        if self.auto_update and len(self.learner.buffer) >= self.learner.update_every:
            # Bootstrap off the value of the just-observed (still-pending) state.
            self.learner.update(last_value=value)

        return decode_action(action_vec, version=self.encoding_version, action_profile=self.action_profile)

    def take_update_batch(self) -> dict | None:
        """Tick-thread hook for an external (off-tick) scheduler: when a segment has filled,
        snapshot + clear the buffer and return the batch, else None. The caller runs
        `learner.optimize(batch)` — on a worker thread if it wants the work off the tick
        path. Only used when `auto_update=False`."""
        if not self.learning or len(self.learner.buffer) < self.learner.update_every:
            return None
        last_value = self._prev.value if self._prev is not None else 0.0
        batch = self.learner.prepare_batch(last_value=last_value)
        if batch is not None and batch["actions"].shape[1] != self.learner.net.action_dim:
            raise ValueError(
                f"PPO batch action width {batch['actions'].shape[1]} != net action_dim "
                f"{self.learner.net.action_dim}"
            )
        return batch

    # -- persistence ---------------------------------------------------------------------
    def state_dict(self) -> dict:
        return self.learner.net.state_dict()

    def save(self, path: str) -> None:
        torch.save(self.learner.net.state_dict(), path)

    def reload_weights(self, checkpoint_path: str) -> None:
        """Hot-swap the live net's weights from a freshly-trained checkpoint (BC or online),
        in place, so a renewed policy takes effect without restarting the simulator. The
        warm-start loader remaps a BC checkpoint onto the actor-critic net (the critic
        re-fits as online learning resumes)."""
        from eflux.agents.ppo.online_net import load_warm_start

        try:
            raw = torch.load(checkpoint_path, map_location="cpu")
            state = raw["state_dict"] if isinstance(raw, dict) and "state_dict" in raw else raw
            incoming_version = infer_encoding_version(state)
            incoming_dim = infer_action_dim(state)
            incoming_profile = infer_action_profile(raw if isinstance(raw, dict) else state)
            incoming_obs_dim = infer_obs_dim(state)
        except Exception:
            log.warning("online PPO hot-reload skipped: cannot inspect %s", checkpoint_path, exc_info=True)
            return
        live_dim = self.learner.net.actor_mean.out_features
        live_obs_dim = self.learner.net.trunk[0].in_features
        if (
            incoming_version != self.encoding_version
            or incoming_dim != live_dim
            or incoming_obs_dim != live_obs_dim
            or incoming_profile != self.action_profile
        ):
            log.warning(
                "online PPO hot-reload skipped: checkpoint encoding V%s/action_dim=%s "
                "profile=%s obs_dim=%s does not match live encoding V%s/action_dim=%s profile=%s obs_dim=%s",
                incoming_version,
                incoming_dim,
                incoming_profile,
                incoming_obs_dim,
                self.encoding_version,
                live_dim,
                self.action_profile,
                live_obs_dim,
            )
            return
        net = load_warm_start(checkpoint_path)
        self.learner.net.load_state_dict(net.state_dict())
        self.obs_version = net.obs_version
        self.action_profile = net.action_profile


def build_online_policy(
    checkpoint_path: str | None = None,
    *,
    learning: bool = True,
    auto_update: bool = True,
    seed: int = 0,
    encoding_version: int | None = None,
) -> OnlinePPOPolicy:
    """Construct an OnlinePPOPolicy, warm-starting the net from a BC or resumed checkpoint
    when given. A missing/unreadable checkpoint falls back to a fresh net (logged)."""
    from eflux.agents.ppo.online_net import ActorCriticNet, load_warm_start
    from eflux.config import get_settings

    net: ActorCriticNet
    if checkpoint_path:
        try:
            net = load_warm_start(checkpoint_path)
        except Exception:
            log.exception("online PPO warm-start failed for %s — fresh net", checkpoint_path)
            version = encoding_version if encoding_version is not None else get_settings().ppo_encoding_version
            net = ActorCriticNet(action_dim=encoding_action_dim(version))
    else:
        version = encoding_version if encoding_version is not None else get_settings().ppo_encoding_version
        net = ActorCriticNet(action_dim=encoding_action_dim(version))
    learner = OnlineLearner(net=net, seed=seed)
    return OnlinePPOPolicy(learner=learner, learning=learning, auto_update=auto_update)
