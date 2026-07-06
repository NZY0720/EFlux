"""Tests for the custom online PPO learner (Part C: M1-M4).

Covers the actor-critic net + BC warm-start parity, the rollout buffer / GAE math, the
clipped-PPO update (policy moves toward advantaged actions), the pure live-reward function,
and the OnlinePPOPolicy seam (one-tick-delayed reward attribution, frozen-eval determinism).
No Ray, no live sim — the VPPPrimitiveEnv is used only to mint realistic contexts.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import torch

from eflux.agents.base import BaseAgent, MarketSnapshot, OrderIntent
from eflux.agents.ppo.bc import BCNet
from eflux.agents.ppo.online_buffer import RolloutBuffer
from eflux.agents.ppo.online_net import (
    ActorCriticNet,
    load_warm_start,
    warm_start_from_bcnet,
)
from eflux.agents.ppo.online_ppo import (
    OnlineLearner,
    OnlinePPOPolicy,
    RewardWeights,
    _Snap,
    build_online_policy,
    compute_step_reward,
)
from eflux.agents.ppo.primitive_encoding import (
    ACTION_DIM,
    ACTION_DIM_V2,
    ENCODING_V1,
    ENCODING_V2,
    N_MODES,
    OBS_DIM,
    PRICE_REF,
    PRIMITIVE_MODES,
    decode_action,
    encode_action,
)
from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv
from eflux.agents.strategy.schema import StrategyMode
from eflux.bridge.bus import InMemoryBus
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams


# -- M1: net + warm-start ----------------------------------------------------------------
def test_actor_critic_forward_shapes():
    net = ActorCriticNet()
    x = torch.zeros(5, OBS_DIM)
    mean, value = net(x)
    assert mean.shape == (5, ACTION_DIM)
    assert value.shape == (5,)


def test_act_returns_vector_logp_value():
    net = ActorCriticNet()
    vec, logp, value = net.act(np.zeros(OBS_DIM, dtype=np.float32))
    assert vec.shape == (ACTION_DIM,)
    assert np.isfinite(vec).all() and np.isfinite(logp) and np.isfinite(value)


def test_warm_start_parity_with_bcnet():
    torch.manual_seed(0)
    bc = BCNet()
    ac = warm_start_from_bcnet(ActorCriticNet(), bc.state_dict())
    rng = np.random.default_rng(0)
    for _ in range(20):
        obs = rng.normal(size=OBS_DIM).astype(np.float32)
        with torch.no_grad():
            bc_vec = bc(torch.as_tensor(obs)).numpy()
        ac_vec = ac.act_mean(obs)
        np.testing.assert_allclose(ac_vec, bc_vec, rtol=1e-5, atol=1e-5)
        # The whole point: a warm-started policy serves the cloned action.
        assert decode_action(ac_vec).mode == decode_action(bc_vec).mode


def test_load_warm_start_autodetects_bc_and_resumed(tmp_path):
    bc_path = tmp_path / "bc.pt"
    torch.save(BCNet().state_dict(), bc_path)
    net = load_warm_start(bc_path)
    assert isinstance(net, ActorCriticNet)
    # Round-trip a full ActorCriticNet checkpoint (resume path).
    ac_path = tmp_path / "ac.pt"
    torch.save(net.state_dict(), ac_path)
    resumed = load_warm_start(ac_path)
    for k, v in net.state_dict().items():
        assert torch.allclose(resumed.state_dict()[k], v)


def test_v1_warm_started_policy_never_sets_price_target_mult(tmp_path):
    bc_path = tmp_path / "bc_v1.pt"
    torch.save(BCNet(encoding_version=ENCODING_V1).state_dict(), bc_path)
    policy = build_online_policy(str(bc_path), learning=False)
    env = VPPPrimitiveEnv({"seed": 8})
    env.reset(seed=8)
    action = policy.select_action(*_ctx_val(env))
    assert policy.encoding_version == ENCODING_V1
    assert action.price_target_mult is None


def test_reload_weights_skips_version_mismatch(tmp_path, caplog):
    live = build_online_policy(learning=False, encoding_version=ENCODING_V1)
    before = {k: v.clone() for k, v in live.state_dict().items()}
    ckpt = tmp_path / "bc_v2.pt"
    torch.save(BCNet(encoding_version=ENCODING_V2).state_dict(), ckpt)

    with caplog.at_level("WARNING"):
        live.reload_weights(str(ckpt))

    assert "hot-reload skipped" in caplog.text
    assert live.learner.net.actor_mean.out_features != ACTION_DIM_V2
    for k, v in live.state_dict().items():
        assert torch.allclose(v, before[k])


# -- M2: buffer + GAE --------------------------------------------------------------------
def test_gae_matches_manual_computation():
    buf = RolloutBuffer()
    rewards = [1.0, 0.0, 2.0, -1.0]
    values = [0.5, 0.4, 0.3, 0.2]
    for r, v in zip(rewards, values, strict=True):
        buf.add(np.zeros(OBS_DIM), np.zeros(ACTION_DIM), logprob=0.0, value=v, reward=r)
    gamma, lam, last_value = 0.99, 0.95, 0.1
    out = buf.compute_gae(last_value=last_value, gamma=gamma, lam=lam)

    n = len(rewards)
    adv = [0.0] * n
    last = 0.0
    for t in range(n - 1, -1, -1):
        nv = values[t + 1] if t + 1 < n else last_value
        delta = rewards[t] + gamma * nv - values[t]  # no dones
        last = delta + gamma * lam * last
        adv[t] = last
    np.testing.assert_allclose(out["advantages"].numpy(), np.array(adv, dtype=np.float32), rtol=1e-5)
    np.testing.assert_allclose(
        out["returns"].numpy(), (np.array(adv) + np.array(values)).astype(np.float32), rtol=1e-5
    )


# -- M3: PPO update ----------------------------------------------------------------------
def test_update_clears_buffer_and_swaps_net():
    learner = OnlineLearner(seed=1, min_update_size=4)
    net_before = learner.net
    rng = np.random.default_rng(1)
    for _ in range(16):
        obs = rng.normal(size=OBS_DIM).astype(np.float32)
        vec, logp, value = learner.net.act(obs)
        learner.buffer.add(obs, vec, logp, value, reward=1.0)
    stats = learner.update(last_value=0.0)
    assert stats is not None and np.isfinite(stats["loss"])
    assert len(learner.buffer) == 0
    assert learner.net is not net_before  # atomic swap to the trained clone


def _stack(rows):
    return torch.as_tensor(np.asarray(rows, dtype=np.float32))


def test_update_widens_logprob_gap_toward_advantaged_actions():
    # Advantages are normalized per-minibatch (zero-mean), so the right signal is
    # *relative*: high-reward actions should gain log-prob vs low-reward ones. value_coef=0
    # isolates the actor surrogate.
    learner = OnlineLearner(
        seed=7, epochs=20, minibatch=128, kl_target=0.0, entropy_coef=0.0, value_coef=0.0
    )
    rng = np.random.default_rng(7)
    hi_obs, hi_act, lo_obs, lo_act = [], [], [], []
    for i in range(128):
        obs = rng.normal(size=OBS_DIM).astype(np.float32)
        vec, logp, value = learner.net.act(obs)
        learner.buffer.add(obs, vec, logp, value, reward=(5.0 if i % 2 == 0 else -5.0))
        (hi_obs if i % 2 == 0 else lo_obs).append(obs)
        (hi_act if i % 2 == 0 else lo_act).append(vec)

    def gap(net):
        with torch.no_grad():
            dh, _ = net.distribution(_stack(hi_obs))
            dl, _ = net.distribution(_stack(lo_obs))
            return float(dh.log_prob(_stack(hi_act)).sum(-1).mean()
                         - dl.log_prob(_stack(lo_act)).sum(-1).mean())

    before = gap(learner.net)
    learner.update(last_value=0.0)
    assert gap(learner.net) > before


def test_update_fits_value_function():
    # The critic should regress toward the GAE returns over an update.
    learner = OnlineLearner(seed=2, epochs=20, minibatch=64, kl_target=0.0, entropy_coef=0.0)
    rng = np.random.default_rng(2)
    for _ in range(96):
        obs = rng.normal(size=OBS_DIM).astype(np.float32)
        vec, logp, value = learner.net.act(obs)
        learner.buffer.add(obs, vec, logp, value, reward=5.0)
    peek = learner.buffer.compute_gae(last_value=0.0)  # peeking does not clear the buffer
    obs_t, returns = peek["obs"], peek["returns"]

    def value_mse(net):
        with torch.no_grad():
            _, v = net(obs_t)
            return float(((v - returns) ** 2).mean())

    before = value_mse(learner.net)
    learner.update(last_value=0.0)
    assert value_mse(learner.net) < before


# -- M4: live reward ---------------------------------------------------------------------
def test_compute_step_reward_matches_formula():
    w = RewardWeights()
    prev = _Snap(pnl=0.0, pending=1.0, open_net=0.0, soc_frac=0.5, soc_kwh=10.0, rejections=0.0)
    cur = _Snap(pnl=30.0, pending=0.0, open_net=0.0, soc_frac=0.9, soc_kwh=11.0, rejections=2.0)
    expected = (
        30.0                                  # realized
        + 0.1 * (((0.0 + 11.0) - (1.0 + 10.0)) * PRICE_REF)  # pending + SOC inventory delta
        - 1.0 * abs(0.0 + 0.0)                # imbalance
        - 5.0 * 0.0                           # soc band: 0.9 is inside [0.1, 0.95]
        - 0.3 * abs(11.0 - 10.0)             # degrade
        - 10.0 * (2.0 - 0.0)                 # 2 rejections
    )
    assert compute_step_reward(prev, cur, w) == expected


def test_compute_step_reward_inventory_includes_soc_delta():
    w = RewardWeights()
    prev = _Snap(pnl=0.0, pending=0.0, open_net=0.0, soc_frac=0.5, soc_kwh=10.0, rejections=0.0)
    cur = _Snap(pnl=0.0, pending=0.0, open_net=0.0, soc_frac=0.55, soc_kwh=11.0, rejections=0.0)

    expected = 0.1 * PRICE_REF - 0.3
    assert compute_step_reward(prev, cur, w) == expected


def test_soc_target_shaping_opt_in():
    base = RewardWeights()
    shaped = RewardWeights(soc_target_weight=2.0)
    prev = _Snap(0.0, 0.0, 0.0, 0.5, 10.0, 0.0, soc_target=0.5)
    cur = _Snap(0.0, 0.0, 0.0, 0.3, 10.0, 0.0, soc_target=0.5)  # drained below target
    assert compute_step_reward(prev, cur, base) == 0.0  # band [0.1,0.95] not breached
    assert compute_step_reward(prev, cur, shaped) < 0.0  # shaping penalizes the drift


def _ctx_val(env):
    ctx = env._make_ctx()
    return ctx, env._oracle.estimate(ctx)


def test_policy_one_tick_delayed_buffering():
    policy = build_online_policy(learning=True, auto_update=False, seed=3)
    env = VPPPrimitiveEnv({"seed": 3})
    env.reset(seed=3)
    ctx, val = _ctx_val(env)
    a0 = policy.select_action(ctx, val)
    assert len(policy.learner.buffer) == 0  # first tick: nothing to finalize yet
    env.step(encode_action(a0))
    ctx, val = _ctx_val(env)
    policy.select_action(ctx, val)
    assert len(policy.learner.buffer) == 1  # prev transition now finalized + buffered


def test_frozen_eval_is_deterministic_and_never_buffers():
    policy = build_online_policy(learning=False, seed=4)
    env = VPPPrimitiveEnv({"seed": 4})
    env.reset(seed=4)
    ctx, val = _ctx_val(env)
    a1 = policy.select_action(ctx, val)
    a2 = policy.select_action(ctx, val)
    assert a1.mode == a2.mode and a1.qty_fraction == a2.qty_fraction
    assert len(policy.learner.buffer) == 0


# -- mode-reg target (used by the LLM meta-controller in M6) ------------------------------
def test_set_mode_target_normalized_and_biased():
    learner = OnlineLearner()
    learner.set_mode_target(
        preferred=(StrategyMode.LIQUIDATE_SURPLUS,), avoid=(StrategyMode.NOOP,)
    )
    q = learner._mode_target.numpy()
    assert q.shape == (N_MODES,)
    np.testing.assert_allclose(q.sum(), 1.0, rtol=1e-6)
    i_pref = PRIMITIVE_MODES.index(StrategyMode.LIQUIDATE_SURPLUS)
    i_avoid = PRIMITIVE_MODES.index(StrategyMode.NOOP)
    assert q[i_pref] > 1.0 / N_MODES > q[i_avoid]


def test_set_mode_target_cleared_when_empty():
    learner = OnlineLearner()
    learner.set_mode_target(preferred=(StrategyMode.NOOP,))
    assert learner._mode_target is not None
    learner.set_mode_target()
    assert learner._mode_target is None


# -- M5: risk rejections surfaced into AgentContext --------------------------------------
class _CaptureAgent(BaseAgent):
    """Records the rejection tally it sees and emits one always-vetoed order (price ≫ max)."""

    def __init__(self) -> None:
        self.seen: list[float] = []

    def decide(self, ctx):
        self.seen.append(ctx.risk_rejections_total)
        return [OrderIntent(side="sell", price=Decimal("2000"), qty=Decimal("1"))]


def test_risk_rejections_surface_into_context():
    sim = Simulator(bus=InMemoryBus())
    agent = _CaptureAgent()
    vpp = sim.add_builtin_vpp("rej", VPPParams(), agent)
    sim_ts = sim.clock.now_sim()
    tick_h = 1.0 / 3600.0
    market = MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot())
    sim._tick_vpp(vpp, sim_ts, tick_h, market)
    sim._tick_vpp(vpp, sim_ts, tick_h, market)
    assert agent.seen[0] == 0.0       # nothing vetoed before the first decision
    assert agent.seen[1] == 1.0       # tick 1's price-2000 order was vetoed → delta of 1


# -- M6: LLM meta-controller -------------------------------------------------------------
def test_policy_apply_meta_scales_weights_and_forwards_levers():
    from eflux.agents.reflective.strategist import MetaControl

    policy = build_online_policy(seed=0)
    base = RewardWeights()
    meta = MetaControl(
        w_soc_mult=2.0, w_imbalance_mult=0.5, w_degrade_mult=1.5,
        lr=1e-4, entropy_coef=0.03, kl_target=0.01, mode_reg_coef=0.5,
    ).clamped()
    policy.apply_meta(meta)
    assert policy.weights.soc == base.soc * 2.0
    assert policy.weights.imbalance == base.imbalance * 0.5
    assert policy.weights.degrade == base.degrade * 1.5
    assert policy.learner.lr == 1e-4
    assert policy.learner.entropy_coef == 0.03
    assert policy.learner.kl_target == 0.01
    assert policy.learner.mode_reg_coef == 0.5
    policy.apply_meta(None)  # stale steer cleared back to baseline
    assert policy.weights == base


def test_mirror_vpp_is_strategist_less_online_twin():
    from eflux.agents.hybrid import StrategyAgent
    from eflux.simulator.agent_spec import AgentSpec, ExecutorSpec
    from eflux.simulator.scenarios import _add_mirror_vpp

    sim = Simulator(bus=InMemoryBus())
    spec = AgentSpec(
        name="llm-x",
        agent="hybrid",
        seed=80,
        params={"battery_kwh": 10.0, "battery_kw_max": 4.0},
        agent_params={"demand_beta": 0.5},
        executor=ExecutorSpec(kind="ppo_online"),  # no checkpoint → fresh net
        mirror=True,
    )
    _add_mirror_vpp(sim, spec, use_real_weather=False, default_seed=80)
    twin = next(v for v in sim.vpps.values() if v.name == "llm-x-ppo-mirror")
    assert isinstance(twin.agent, StrategyAgent)
    assert not hasattr(twin.agent, "strategist")           # PPO-only control, no LLM
    assert hasattr(twin.agent._policy, "learner")           # learns online
    assert twin.agent._policy.auto_update is True           # sync inline (no scheduler)


def test_persist_online_weights(tmp_path, monkeypatch):
    from eflux.agents.hybrid import StrategyAgent
    from eflux.config import get_settings

    monkeypatch.setenv("EFLUX_ONLINE_LEARNING_SAVE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        sim = Simulator(bus=InMemoryBus())
        sim.add_builtin_vpp(
            "learner-x", VPPParams(), StrategyAgent(policy=build_online_policy(seed=0))
        )
        sim.add_builtin_vpp("plain", VPPParams(), _CaptureAgent())  # no online policy → skipped
        sim._persist_online_weights()
        assert (tmp_path / "learner-x.pt").exists()
        assert not (tmp_path / "plain.pt").exists()
    finally:
        get_settings.cache_clear()


def test_hybrid_offtick_update_sync_fallback_without_loop():
    from eflux.agents.hybrid import HybridPolicyAgent
    from eflux.agents.ppo.online_ppo import OnlineLearner

    learner = OnlineLearner(update_every=8, min_update_size=4, seed=0)
    policy = OnlinePPOPolicy(learner=learner, learning=True, auto_update=False)  # async mode
    agent = HybridPolicyAgent(executor=policy, strategist=None)
    env = VPPPrimitiveEnv({"seed": 0})
    env.reset(seed=0)
    for _ in range(24):
        agent.decide(env._make_ctx())
        env.step(np.zeros(ACTION_DIM, dtype=np.float32))
    # No running loop → _maybe_online_update runs the PPO step synchronously.
    assert learner.update_count >= 1


def test_mode_reg_loss_pulls_policy_toward_target():
    # With zero advantages and value_coef=0, only the mode-reg term has gradient — it must
    # shift the policy's mode distribution toward the LLM-preferred mode.
    target_idx = 1
    learner = OnlineLearner(
        seed=11, epochs=40, minibatch=64, kl_target=0.0,
        entropy_coef=0.0, value_coef=0.0, mode_reg_coef=1.0,
    )
    learner.set_mode_target(preferred=(PRIMITIVE_MODES[target_idx],))
    rng = np.random.default_rng(11)
    obs_rows = []
    for _ in range(64):
        obs = rng.normal(size=OBS_DIM).astype(np.float32)
        vec, logp, value = learner.net.act(obs)
        learner.buffer.add(obs, vec, logp, value, reward=0.0)
        obs_rows.append(obs)
    obs_t = _stack(obs_rows)

    def mode_mass(net):
        with torch.no_grad():
            mean, _ = net(obs_t)
            return float(torch.softmax(mean[:, :N_MODES], dim=-1)[:, target_idx].mean())

    before = mode_mass(learner.net)
    learner.update(last_value=0.0)
    assert mode_mass(learner.net) > before
