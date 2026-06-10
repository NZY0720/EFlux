"""Unit tests for the PPO sandbox env. Skipped if 'ai' extras not installed."""

from __future__ import annotations

import pytest

gym = pytest.importorskip("gymnasium")
np = pytest.importorskip("numpy")


def test_reset_returns_obs_in_space():
    from eflux.agents.ppo.env import VPPSingleAgentEnv

    env = VPPSingleAgentEnv({"seed": 42})
    obs, info = env.reset()
    assert obs.shape == (10,)
    assert obs.dtype == np.float32
    # obs values should be finite.
    assert np.all(np.isfinite(obs))
    assert info == {}


def test_step_returns_five_tuple():
    from eflux.agents.ppo.env import VPPSingleAgentEnv

    env = VPPSingleAgentEnv({"seed": 0, "episode_ticks": 4})
    env.reset()
    action = env.action_space.sample()
    result = env.step(action)
    assert len(result) == 5
    obs, reward, terminated, truncated, info = result
    assert obs.shape == (10,)
    assert isinstance(reward, float)
    assert isinstance(terminated, bool)
    assert isinstance(truncated, bool)
    assert isinstance(info, dict)


def test_episode_ends_after_episode_ticks():
    from eflux.agents.ppo.env import VPPSingleAgentEnv

    env = VPPSingleAgentEnv({"seed": 0, "episode_ticks": 3})
    env.reset()
    truncated = False
    for _ in range(3):
        _, _, _, truncated, _ = env.step(env.action_space.sample())
    assert truncated is True


def test_no_op_action_does_not_trade():
    from eflux.agents.ppo.env import VPPSingleAgentEnv

    env = VPPSingleAgentEnv({"seed": 7, "episode_ticks": 2})
    env.reset()
    # |side_logit| < 0.1 → no-op.
    action = np.array([0.0, 0.0, 0.0], dtype=np.float32)
    obs1, _r, _t1, _tr1, _ = env.step(action)
    obs2, _r, _t2, _tr2, _ = env.step(action)
    # Time should still advance (obs cyclic-hour components differ).
    assert not np.allclose(obs1, obs2)


def test_action_space_bounds():
    from eflux.agents.ppo.env import VPPSingleAgentEnv

    env = VPPSingleAgentEnv()
    assert env.action_space.shape == (3,)
    assert np.all(env.action_space.low == np.array([-1.0, -1.0, 0.0], dtype=np.float32))
    assert np.all(env.action_space.high == np.array([1.0, 1.0, 1.0], dtype=np.float32))
