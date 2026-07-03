from __future__ import annotations

import numpy as np
import pytest

from eflux.agents.ppo.bc import BCNet
from eflux.agents.ppo.online_net import ActorCriticNet
from eflux.agents.ppo.primitive_encoding import (
    ACTION_DIM,
    ACTION_DIM_V1,
    ACTION_DIM_V2,
    ENCODING_V1,
    ENCODING_V2,
    decode_action,
    encode_action,
    infer_encoding_version,
)
from eflux.agents.strategy.schema import StrategyAction, StrategyMode


def test_action_dim_alias_is_v1():
    assert ACTION_DIM == ACTION_DIM_V1


def test_v1_encode_decode_never_sets_price_target_mult():
    action = StrategyAction(
        mode=StrategyMode.COVER_DEFICIT,
        aggressiveness=0.25,
        qty_fraction=0.75,
        price_offset_bps=8.0,
        soc_target=0.6,
        price_target_mult=1.7,
    )
    vec = encode_action(action, version=ENCODING_V1)
    decoded = decode_action(vec, version=ENCODING_V1)
    assert vec.shape == (ACTION_DIM_V1,)
    assert decoded.mode is action.mode
    assert decoded.price_target_mult is None


def test_v2_encode_decode_round_trips_price_target_mult():
    action = StrategyAction(
        mode=StrategyMode.LIQUIDATE_SURPLUS,
        aggressiveness=0.3,
        qty_fraction=0.8,
        price_offset_bps=10.0,
        soc_target=0.55,
        price_target_mult=1.6,
    )
    vec = encode_action(action, version=ENCODING_V2)
    decoded = decode_action(vec, version=ENCODING_V2)
    assert vec.shape == (ACTION_DIM_V2,)
    assert decoded.mode is action.mode
    assert decoded.aggressiveness == pytest.approx(action.aggressiveness, abs=0.02)
    assert decoded.qty_fraction == pytest.approx(action.qty_fraction, abs=0.02)
    assert decoded.price_offset_bps == pytest.approx(action.price_offset_bps, abs=0.5)
    assert decoded.soc_target == pytest.approx(action.soc_target, abs=0.02)
    assert decoded.price_target_mult == pytest.approx(action.price_target_mult, rel=1e-5)


def test_v2_none_encodes_as_neutral_multiplier():
    decoded = decode_action(encode_action(StrategyAction(), version=ENCODING_V2), version=ENCODING_V2)
    assert decoded.price_target_mult == pytest.approx(1.0)


def test_infer_encoding_version_from_real_bc_state_dicts():
    assert infer_encoding_version(BCNet(encoding_version=ENCODING_V1).state_dict()) == ENCODING_V1
    assert infer_encoding_version(BCNet(encoding_version=ENCODING_V2).state_dict()) == ENCODING_V2


def test_infer_encoding_version_from_real_actor_critic_state_dicts():
    assert infer_encoding_version(ActorCriticNet(action_dim=ACTION_DIM_V1).state_dict()) == ENCODING_V1
    assert infer_encoding_version(ActorCriticNet(action_dim=ACTION_DIM_V2).state_dict()) == ENCODING_V2


def test_decode_rejects_wrong_width_for_version():
    with pytest.raises(ValueError):
        decode_action(np.zeros(ACTION_DIM_V1, dtype=np.float32), version=ENCODING_V2)
