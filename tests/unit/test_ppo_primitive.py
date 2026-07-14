"""Tests for the structured-action PPO env + encoding (plumbing only, no Ray/training).

Mirrors the existing test_ppo_env.py philosophy: validate the obs/action plumbing and the
env's gym contract without standing up Ray. Checkpoint round-trip is validated out of band
via `eflux.agents.ppo.eval`.
"""

from __future__ import annotations

import numpy as np
import pytest

from eflux.agents.ppo.primitive_encoding import (
    ACTION_DIM,
    OBS_DIM,
    PRIMITIVE_MODES,
    decode_action,
    encode_action,
)
from eflux.agents.ppo.primitive_env import (
    COUNTERPARTY_ID,
    SOC_HIGH,
    SOC_LOW,
    W_SOC,
    VPPPrimitiveEnv,
)
from eflux.agents.strategy.schema import StrategyAction, StrategyMode


def test_decode_action_selects_mode_by_argmax():
    vec = np.zeros(ACTION_DIM, dtype=np.float32)
    vec[2] = 5.0  # highest logit at mode index 2
    assert decode_action(vec).mode == PRIMITIVE_MODES[2]


def test_decode_action_params_stay_in_range():
    rng = np.random.default_rng(0)
    for _ in range(100):
        a = decode_action(rng.uniform(-6, 6, ACTION_DIM).astype(np.float32))
        assert isinstance(a, StrategyAction)
        assert 0.0 <= a.aggressiveness <= 1.0
        assert 0.0 <= a.qty_fraction <= 1.0
        assert -50.0 <= a.price_offset_bps <= 50.0
        assert 0.0 <= a.soc_target <= 1.0


def test_behavior_clone_neutral_parameter_residuals_snap_to_exact_defaults():
    encoded = encode_action(StrategyAction(mode=PRIMITIVE_MODES[1]))
    # Model-fit residuals around the supervised target must not move a fair-price
    # order across the spread or trim a physically required balance quantity.
    encoded[-5] += 0.1
    encoded[-4] -= 0.1
    encoded[-3] = 0.04
    encoded[-1] = 0.01

    action = decode_action(encoded)

    assert action.aggressiveness == 0.0
    assert action.qty_fraction == 1.0
    assert action.price_offset_bps == 0.0
    assert action.price_target_mult == 1.0


def test_env_reset_obs_shape_and_finite():
    obs, _ = VPPPrimitiveEnv({"seed": 1}).reset(seed=1)
    assert obs.shape == (OBS_DIM,)
    assert np.isfinite(obs).all()


def test_env_episode_steps_finite_and_truncates():
    env = VPPPrimitiveEnv({"seed": 2, "episode_ticks": 12})
    env.reset(seed=2)
    rng = np.random.default_rng(2)
    steps, done = 0, False
    while not done and steps < 100:
        obs, r, term, trunc, _ = env.step(rng.uniform(-2, 2, ACTION_DIM).astype(np.float32))
        assert np.isfinite(obs).all() and np.isfinite(r)
        done = term or trunc
        steps += 1
    assert steps == 12  # truncates exactly at episode_ticks


def test_env_is_deterministic_given_seed():
    def rollout() -> float:
        env = VPPPrimitiveEnv({"seed": 5, "episode_ticks": 10})
        env.reset(seed=5)
        rng = np.random.default_rng(5)
        return sum(
            env.step(rng.uniform(-2, 2, ACTION_DIM).astype(np.float32))[1] for _ in range(10)
        )

    assert rollout() == rollout()


def test_noop_action_keeps_reward_finite():
    env = VPPPrimitiveEnv({"seed": 3, "episode_ticks": 5})
    env.reset(seed=3)
    noop = np.zeros(ACTION_DIM, dtype=np.float32)  # argmax → NOOP
    for _ in range(5):
        _, r, _, _, _ = env.step(noop)
        assert np.isfinite(r)


def test_realprice_env_trades_with_registered_system_grid():
    env = VPPPrimitiveEnv({"seed": 0, "episode_ticks": 1, "market_mode": "realprice"})
    env.reset(seed=0)
    action = encode_action(
        StrategyAction(mode=StrategyMode.COVER_DEFICIT, aggressiveness=1.0),
        market_mode="realprice",
    )

    env.step(action)

    assert env._gateway.participants[COUNTERPARTY_ID].is_system
    assert env._engine.trade_count >= 1


def test_soc_reward_band_allows_high_solar_charge():
    assert SOC_LOW == 0.1
    assert SOC_HIGH == 0.95
    assert W_SOC == 0.02

    soc_dev_90 = max(0.0, SOC_LOW - 0.9) + 0.25 * max(0.0, 0.9 - SOC_HIGH)
    soc_dev_empty = max(0.0, SOC_LOW - 0.05) + 0.25 * max(0.0, 0.05 - SOC_HIGH)
    soc_dev_full = max(0.0, SOC_LOW - 1.0) + 0.25 * max(0.0, 1.0 - SOC_HIGH)

    assert soc_dev_90 == 0.0
    assert W_SOC * soc_dev_empty == pytest.approx(0.001)
    assert W_SOC * soc_dev_full == pytest.approx(0.00025)
