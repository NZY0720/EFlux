from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pytest
import torch

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.ppo.bc import BCNet
from eflux.agents.ppo.online_net import ActorCriticNet
from eflux.agents.ppo.primitive_encoding import (
    ACTION_DIM,
    ACTION_DIM_V1,
    ACTION_DIM_V2,
    ACTION_PROFILE_P2P,
    ACTION_PROFILE_REALPRICE_GRID,
    ENCODING_V1,
    ENCODING_V2,
    OBS_DIM_V1,
    OBS_DIM_V3,
    OBS_DIM_V4,
    OBS_V1,
    OBS_V3,
    action_dim,
    action_profile_for_action_dim,
    decode_action,
    encode_action,
    encode_obs,
    infer_action_profile,
    infer_encoding_version,
    infer_obs_dim,
    primitive_modes_for,
    set_price_ref_scale,
)
from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv
from eflux.agents.strategy.schema import StrategyAction, StrategyMode
from eflux.agents.valuation import ValuationSignal
from eflux.forecasting.schema import ForecastBundle, ForecastPoint, TargetForecast
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def test_action_dim_alias_is_current_v2():
    assert ACTION_DIM == ACTION_DIM_V2


def test_p2p_default_action_encoding_bytes_are_legacy():
    action = StrategyAction(
        mode=StrategyMode.LIQUIDATE_SURPLUS,
        aggressiveness=0.25,
        qty_fraction=0.75,
        price_offset_bps=8.0,
        soc_target=0.6,
        price_target_mult=1.7,
    )
    expected = np.full(ACTION_DIM_V2, -1.0, dtype=np.float32)
    expected[1] = 1.0
    expected[4] = np.float32(-1.0986123)
    expected[5] = np.float32(1.0986123)
    expected[6] = np.float32(0.1613867)
    expected[7] = np.float32(0.4054651)
    expected[8] = np.float32(0.6611146)
    assert encode_action(action, version=ENCODING_V2).tobytes() == expected.tobytes()


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


def test_realprice_grid_encode_decode_round_trips_six_mode_head():
    modes = primitive_modes_for(action_profile=ACTION_PROFILE_REALPRICE_GRID)
    assert len(modes) == 6
    assert action_dim(ENCODING_V1, action_profile=ACTION_PROFILE_REALPRICE_GRID) == 10
    assert action_dim(ENCODING_V2, action_profile=ACTION_PROFILE_REALPRICE_GRID) == 11
    for mode in modes:
        action = StrategyAction(
            mode=mode,
            aggressiveness=0.3,
            qty_fraction=0.8,
            price_offset_bps=10.0,
            soc_target=0.55,
            price_target_mult=1.4,
        )
        vec = encode_action(action, version=ENCODING_V2, action_profile=ACTION_PROFILE_REALPRICE_GRID)
        decoded = decode_action(vec, version=ENCODING_V2, action_profile=ACTION_PROFILE_REALPRICE_GRID)
        assert vec.shape == (11,)
        assert decoded.mode is mode
        assert decoded.price_target_mult == pytest.approx(action.price_target_mult, rel=1e-5)


def test_action_profile_inference_keys_off_action_dim_not_market_meta():
    legacy_realprice = {
        "market_mode": "realprice",
        "state_dict": BCNet(obs_dim=OBS_DIM_V3, action_dim=ACTION_DIM_V2).state_dict(),
    }
    new_realprice = {
        "market_mode": "realprice",
        "state_dict": BCNet(
            obs_dim=OBS_DIM_V3,
            action_dim=action_dim(ENCODING_V2, action_profile=ACTION_PROFILE_REALPRICE_GRID),
            action_profile=ACTION_PROFILE_REALPRICE_GRID,
        ).state_dict(),
    }
    explicit = dict(legacy_realprice, action_profile=ACTION_PROFILE_REALPRICE_GRID)
    assert action_profile_for_action_dim(8) == ACTION_PROFILE_P2P
    assert action_profile_for_action_dim(9) == ACTION_PROFILE_P2P
    assert action_profile_for_action_dim(10) == ACTION_PROFILE_REALPRICE_GRID
    assert action_profile_for_action_dim(11) == ACTION_PROFILE_REALPRICE_GRID
    assert infer_action_profile(legacy_realprice) == ACTION_PROFILE_P2P
    assert infer_action_profile(new_realprice) == ACTION_PROFILE_REALPRICE_GRID
    assert infer_action_profile(explicit) == ACTION_PROFILE_REALPRICE_GRID


def test_infer_encoding_version_from_real_bc_state_dicts():
    assert infer_encoding_version(BCNet(encoding_version=ENCODING_V1).state_dict()) == ENCODING_V1
    assert infer_encoding_version(BCNet(encoding_version=ENCODING_V2).state_dict()) == ENCODING_V2


def test_infer_encoding_version_from_real_actor_critic_state_dicts():
    assert infer_encoding_version(ActorCriticNet(action_dim=ACTION_DIM_V1).state_dict()) == ENCODING_V1
    assert infer_encoding_version(ActorCriticNet(action_dim=ACTION_DIM_V2).state_dict()) == ENCODING_V2


def test_decode_rejects_wrong_width_for_version():
    with pytest.raises(ValueError):
        decode_action(np.zeros(ACTION_DIM_V1, dtype=np.float32), version=ENCODING_V2)


def _target(value_1h: float, value_12h: float) -> TargetForecast:
    return TargetForecast(
        h5m=ForecastPoint(value_1h),
        h1h=ForecastPoint(value_1h),
        h12h=ForecastPoint(value_12h),
    )


def _forecast(*, price_real_1h: float = 60.0, price_real_12h: float = 70.0) -> ForecastBundle:
    return ForecastBundle(
        as_of=datetime(2024, 1, 1, tzinfo=UTC),
        model_version="test",
        price_real=_target(price_real_1h, price_real_12h),
        price_p2p=_target(55.0, 65.0),
        ghi=_target(500.0, 900.0),
        temp_air=_target(0.0, 0.0),
        wind_speed=_target(0.0, 0.0),
    )


def _ctx(*, forecast: ForecastBundle | None = None) -> AgentContext:
    params = VPPParams(pv_kw_peak=8.0, battery_kwh=10.0, battery_kw_max=4.0, load_kw_base=4.0)
    ts = datetime(2024, 1, 1, 6, 30, tzinfo=UTC)
    state = VPPState(sim_ts=ts, soc_kwh=4.0, pv_kw=2.0, load_kw=3.0)
    state.update_net()
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(kw_peak=params.pv_kw_peak),
        battery=Battery(capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max, soc_kwh=4.0),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=MarketSnapshot(
            sim_ts=ts,
            best_bid=Decimal("45"),
            best_ask=Decimal("55"),
            last_price=Decimal("52"),
            mid_price=Decimal("50"),
        ),
        rng=None,  # encode_obs does not read rng.
        tick_duration_h=1.0,
        open_orders_net_kwh=2.0,
        forecast=forecast,
    )


def _valuation() -> ValuationSignal:
    return ValuationSignal(
        fair_buy_price=60.0,
        fair_sell_price=40.0,
        marginal_battery_value=50.0,
        battery_sell_price=55.0,
        battery_buy_price=45.0,
        surplus_kwh=1.5,
        deficit_kwh=0.5,
        soc_frac=0.4,
        soc_pressure=-0.2,
    )


def test_encode_obs_v1_is_stable_and_default_is_v4():
    set_price_ref_scale(50.0)
    obs_default = encode_obs(_ctx(), _valuation())
    obs_v1 = encode_obs(_ctx(forecast=_forecast()), _valuation(), obs_version=OBS_V1)
    expected = np.array(
        [
            0.25,
            0.375,
            0.4,
            np.sin(2 * np.pi * 6.5 / 24.0),
            np.cos(2 * np.pi * 6.5 / 24.0),
            -0.1,
            0.1,
            1.0,
            0.2,
            1.04,
            0.15,
            0.05,
            1.2,
            0.8,
            1.1,
            0.9,
            -0.2,
            0.2,
        ],
        dtype=np.float32,
    )
    assert obs_default.shape == (OBS_DIM_V4,)
    assert obs_v1.shape == (OBS_DIM_V1,)
    assert obs_v1.tobytes() == expected.tobytes()
    assert obs_default[:OBS_DIM_V1].tobytes() == obs_v1.tobytes()


def test_encode_obs_v3_appends_forecast_channels():
    set_price_ref_scale(50.0)
    obs_v1 = encode_obs(_ctx(), _valuation(), obs_version=OBS_V1)
    obs_v3 = encode_obs(_ctx(forecast=_forecast()), _valuation(), obs_version=OBS_V3)
    assert obs_v3.shape == (OBS_DIM_V3,)
    assert obs_v3[:OBS_DIM_V1].tobytes() == obs_v1.tobytes()
    np.testing.assert_allclose(obs_v3[18:], np.array([0.2, 0.4, 0.1, 0.3, 0.5, 0.9], dtype=np.float32))


def test_encode_obs_v3_without_forecast_zero_fills_new_channels():
    obs = encode_obs(_ctx(forecast=None), _valuation(), obs_version=OBS_V3)
    assert obs.shape == (OBS_DIM_V3,)
    np.testing.assert_array_equal(obs[18:], np.zeros(6, dtype=np.float32))


def test_encode_obs_v3_real_price_direction_channel_signs():
    rising = encode_obs(_ctx(forecast=_forecast(price_real_1h=60.0)), _valuation(), obs_version=OBS_V3)
    falling = encode_obs(_ctx(forecast=_forecast(price_real_1h=40.0)), _valuation(), obs_version=OBS_V3)
    assert rising[18] > 0
    assert falling[18] < 0


def test_infer_obs_dim_from_fake_state_dicts():
    assert infer_obs_dim({"trunk.0.weight": torch.zeros(64, 18)}) == OBS_DIM_V1
    assert infer_obs_dim({"state_dict": {"net.0.weight": torch.zeros(64, 24)}}) == OBS_DIM_V3


def test_load_warm_start_preserves_checkpoint_obs_version(tmp_path):
    from eflux.agents.ppo.bc import save_bc
    from eflux.agents.ppo.online_net import load_warm_start

    path_v1 = tmp_path / "ac_v1.pt"
    torch.save(
        ActorCriticNet(obs_dim=OBS_DIM_V1, action_dim=ACTION_DIM_V1).state_dict(), path_v1
    )
    loaded_v1 = load_warm_start(path_v1)
    assert loaded_v1.obs_version == OBS_V1
    assert loaded_v1.act_mean(np.zeros(OBS_DIM_V1, dtype=np.float32)).shape == (ACTION_DIM_V1,)

    path_v3 = tmp_path / "ac_v3.pt"
    torch.save(
        ActorCriticNet(obs_dim=OBS_DIM_V3, action_dim=ACTION_DIM_V1).state_dict(), path_v3
    )
    loaded_v3 = load_warm_start(path_v3)
    assert loaded_v3.obs_version == OBS_V3
    assert loaded_v3.act_mean(np.zeros(OBS_DIM_V3, dtype=np.float32)).shape == (ACTION_DIM_V1,)

    bc_path_v3 = tmp_path / "bc_v3.pt"
    save_bc(
        BCNet(
            obs_dim=OBS_DIM_V3,
            action_dim=ACTION_DIM_V1,
            obs_version=OBS_V3,
            encoding_version=ENCODING_V1,
        ),
        str(bc_path_v3),
        obs_version=OBS_V3,
        encoding_version=ENCODING_V1,
    )
    loaded_bc_v3 = load_warm_start(bc_path_v3)
    assert loaded_bc_v3.obs_version == OBS_V3
    assert loaded_bc_v3.trunk[0].in_features == OBS_DIM_V3
    assert loaded_bc_v3.act_mean(np.zeros(OBS_DIM_V3, dtype=np.float32)).shape == (ACTION_DIM_V1,)


def test_load_warm_start_resolves_legacy_and_grid_action_profiles(tmp_path):
    from eflux.agents.ppo.bc import save_bc
    from eflux.agents.ppo.online_net import load_warm_start

    legacy_path = tmp_path / "legacy_realprice.pt"
    legacy = BCNet(obs_dim=OBS_DIM_V3, action_dim=ACTION_DIM_V2, obs_version=OBS_V3)
    torch.save({"state_dict": legacy.state_dict(), "market_mode": "realprice", "obs_version": OBS_V3}, legacy_path)
    loaded_legacy = load_warm_start(legacy_path)
    assert loaded_legacy.action_profile == ACTION_PROFILE_P2P
    assert loaded_legacy.actor_mean.out_features == ACTION_DIM_V2

    grid_path = tmp_path / "realprice_grid.pt"
    grid_dim = action_dim(ENCODING_V2, action_profile=ACTION_PROFILE_REALPRICE_GRID)
    grid = BCNet(
        obs_dim=OBS_DIM_V3,
        action_dim=grid_dim,
        obs_version=OBS_V3,
        action_profile=ACTION_PROFILE_REALPRICE_GRID,
    )
    with torch.no_grad():
        grid.net[-1].weight.zero_()
        grid.net[-1].bias.fill_(-5.0)
        grid.net[-1].bias[3] = 5.0
    save_bc(
        grid,
        str(grid_path),
        market_mode="realprice",
        obs_version=OBS_V3,
        encoding_version=ENCODING_V2,
        action_profile=ACTION_PROFILE_REALPRICE_GRID,
    )
    loaded_grid = load_warm_start(grid_path)
    assert loaded_grid.action_profile == ACTION_PROFILE_REALPRICE_GRID
    assert loaded_grid.actor_mean.out_features == grid_dim
    action = decode_action(
        loaded_grid.act_mean(np.zeros(OBS_DIM_V3, dtype=np.float32)),
        version=ENCODING_V2,
        action_profile=loaded_grid.action_profile,
    )
    assert action.mode is StrategyMode.GRID_CHARGE_ON_DIP


def test_v3_primitive_env_reset_and_step_return_finite_24_wide_obs():
    env = VPPPrimitiveEnv({"seed": 11, "episode_ticks": 2, "obs_version": OBS_V3})
    obs, _ = env.reset(seed=11)
    assert obs.shape == (OBS_DIM_V3,)
    assert np.isfinite(obs).all()
    obs, reward, terminated, truncated, _ = env.step(np.zeros(ACTION_DIM, dtype=np.float32))
    assert obs.shape == (OBS_DIM_V3,)
    assert np.isfinite(obs).all()
    assert np.isfinite(reward)
    assert not terminated
    assert not truncated
