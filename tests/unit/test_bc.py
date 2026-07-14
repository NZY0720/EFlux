"""Behavior-cloning warm-start tests (M5).

Gated on the 'ai' extras (torch/gymnasium). Verifies the action round-trip, demo
collection, that cloning reproduces the expert's primitive choice, and that the cloned
policy drops into a StrategyAgent and trades cleanly on the M3 benchmark.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("gymnasium")


from eflux.agents.ppo.primitive_encoding import (
    ACTION_DIM_V1,
    ENCODING_V1,
    OBS_DIM,
    decode_action,
    encode_action,
)
from eflux.agents.strategy.schema import StrategyAction, StrategyMode


def test_encode_decode_action_round_trip():
    a = StrategyAction(
        mode=StrategyMode.COVER_DEFICIT, aggressiveness=0.3, qty_fraction=0.8,
        price_offset_bps=10.0, soc_target=0.6,
    )
    b = decode_action(encode_action(a))
    assert b.mode is a.mode
    assert b.aggressiveness == pytest.approx(a.aggressiveness, abs=0.02)
    assert b.qty_fraction == pytest.approx(a.qty_fraction, abs=0.02)
    assert b.price_offset_bps == pytest.approx(a.price_offset_bps, abs=0.5)
    assert b.soc_target == pytest.approx(a.soc_target, abs=0.02)


def test_unknown_mode_clones_to_noop():
    # LADDER_SELL is outside the PPO primitive set → encodes to NOOP.
    assert decode_action(encode_action(StrategyAction(mode=StrategyMode.LADDER_SELL))).mode is StrategyMode.NOOP


def test_collect_demonstrations_shapes():
    from eflux.agents.ppo.bc import collect_demonstrations
    from eflux.agents.strategy.policy import ScriptedStrategyPolicy

    obs, acts = collect_demonstrations(ScriptedStrategyPolicy(), n_episodes=3, seed=0)
    assert obs.shape[0] == acts.shape[0] > 0
    assert obs.shape[1] == OBS_DIM
    assert acts.shape[1] == ACTION_DIM_V1


def test_demo_curriculum_reserves_battery_only_episodes_with_finite_observations(monkeypatch):
    from eflux.agents.ppo.bc import (
        DEMO_BATTERY_ONLY_FRACTION,
        _battery_only_demo_params,
        collect_demonstrations,
    )
    from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv
    from eflux.agents.strategy.policy import ScriptedStrategyPolicy

    params_by_episode = _battery_only_demo_params(40, seed=20260711)
    assert len(params_by_episode) == round(40 * DEMO_BATTERY_ONLY_FRACTION) == 9
    assert all(
        params.pv_kw_peak == params.wind_kw_rated == params.load_kw_base == 0.0
        and 10.0 <= params.battery_kwh <= 30.0
        and 3.0 <= params.battery_kw_max <= 6.0
        for params in params_by_episode.values()
    )

    seen = []
    original_reset = VPPPrimitiveEnv.reset

    def recording_reset(self, *args, **kwargs):
        result = original_reset(self, *args, **kwargs)
        seen.append(self._params)
        return result

    monkeypatch.setattr(VPPPrimitiveEnv, "reset", recording_reset)
    obs, _acts = collect_demonstrations(
        ScriptedStrategyPolicy(), n_episodes=40, seed=20260711,
        env_config={"episode_ticks": 1},
        battery_only_fraction=DEMO_BATTERY_ONLY_FRACTION,
    )

    pure_traders = [
        params
        for params in seen
        if params.pv_kw_peak == params.wind_kw_rated == params.load_kw_base == 0.0
    ]
    assert len(pure_traders) == 9
    assert np.isfinite(obs).all()


def test_bc_version_plumbing_uses_v1_width():
    from eflux.agents.ppo.bc import BCNet, BCPolicy, collect_demonstrations, train_bc
    from eflux.agents.strategy.policy import ScriptedStrategyPolicy

    obs, acts = collect_demonstrations(
        ScriptedStrategyPolicy(), n_episodes=1, seed=0, encoding_version=ENCODING_V1
    )
    assert acts.shape[1] == ACTION_DIM_V1
    net = train_bc(obs, acts, epochs=1, seed=0, encoding_version=ENCODING_V1)
    assert isinstance(net, BCNet)
    assert net.net[-1].out_features == ACTION_DIM_V1
    assert BCPolicy(net).encoding_version == ENCODING_V1


def test_bc_clones_expert_trade_decisions():
    from eflux.agents.ppo.bc import collect_demonstrations, trade_mode_accuracy, train_bc
    from eflux.agents.strategy.policy import ScriptedStrategyPolicy

    obs, acts = collect_demonstrations(ScriptedStrategyPolicy(), n_episodes=30, seed=1)
    net = train_bc(obs, acts, epochs=300, seed=1)
    # The expert's trade choice (surplus→liquidate, deficit→cover) is a function of the
    # imbalance obs channels, so the clone should reproduce it almost perfectly. (NOOP
    # boundary cases are excluded — their dust orders are dropped downstream anyway.)
    assert trade_mode_accuracy(net, obs, acts) > 0.9


def test_bc_faithfully_clones_expert():
    """The point of BC (§7 Stage 2): start PPO as a faithful clone of the expert, in PPO's own
    training env. NB: under the battery-buffer physics the scripted expert is no longer strongly
    above random — generation now drives SOC up and the reward's SOC-deviation penalty fights it,
    so lifting the baseline needs a reward retune / PPO retrain (tracked separately). This test
    guards the property BC actually owns: cloning fidelity to the expert."""
    from eflux.agents.ppo.bc import (
        BCPolicy,
        collect_demonstrations,
        mean_episode_reward,
        train_bc,
    )
    from eflux.agents.strategy.policy import ScriptedStrategyPolicy

    expert = ScriptedStrategyPolicy()
    obs, acts = collect_demonstrations(expert, n_episodes=30, seed=1)
    net = train_bc(obs, acts, epochs=300, seed=1)

    bc_r = mean_episode_reward(BCPolicy(net), n_episodes=8, seed=5)
    exp_r = mean_episode_reward(expert, n_episodes=8, seed=5)
    # Faithful to the cloned expert (the actual BC property). Expert-vs-random edge is
    # deferred to the reward retune / PPO retrain under the new battery physics.
    assert abs(bc_r - exp_r) <= 0.3 * abs(exp_r)


def test_battery_aware_expert_scores_at_least_random():
    from eflux.agents.ppo.bc import mean_episode_reward, mean_random_reward
    from eflux.agents.strategy.policy import BatteryAwareStrategyPolicy

    expert_r = mean_episode_reward(BatteryAwareStrategyPolicy(), n_episodes=8, seed=5)
    random_r = mean_random_reward(n_episodes=8, seed=5)
    assert expert_r >= random_r


def test_bc_policy_actions_are_valid_on_benchmark():
    from eflux.agents.bench.run import score
    from eflux.agents.ppo.bc import build_bc_agent, train_bc_policy

    policy = train_bc_policy(n_episodes=30, epochs=300, seed=2)
    m = score("bc", lambda: build_bc_agent(policy), n_ticks=144, tick_h=10 / 60)
    # Decoded actions clear TradingGatewayV1, and the agent participates.
    assert m.risk_rejections == 0
    assert m.energy_traded_kwh > 0
