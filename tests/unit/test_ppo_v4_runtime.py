from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from eflux.agents.base import AgentContext, MarketSnapshot, OpenOrderView
from eflux.agents.ppo.bc import BCNet
from eflux.agents.ppo.online_ppo import RewardWeights, _Snap, compute_step_reward
from eflux.agents.ppo.primitive_encoding import (
    OBS_DIM_V4,
    OBS_V4,
    encode_obs,
)
from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv
from eflux.agents.valuation import ValuationSignal
from eflux.market.ledger import LedgerCategory
from eflux.market.products import DeliveryInterval
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _context() -> AgentContext:
    now = datetime(2026, 7, 11, 12, 4, 30, tzinfo=UTC)
    start = now + timedelta(seconds=30)
    interval = DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )
    params = VPPParams(
        pv_kw_peak=4,
        battery_kwh=10,
        battery_kw_max=4,
        load_kw_base=2,
        starting_cash_usd=1,
    )
    state = VPPState(
        sim_ts=now,
        soc_kwh=5,
        pv_kw=1,
        load_kw=2,
        pnl=Decimal("-0.02"),
    )
    state.update_net()
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(4),
        battery=Battery(10, 4, soc_kwh=5),
        load=FlexibleLoad(2),
        market=MarketSnapshot(
            sim_ts=now,
            best_bid=Decimal("-20"),
            best_ask=Decimal("-10"),
            last_price=Decimal("-15"),
            mid_price=Decimal("-15"),
        ),
        rng=random.Random(0),
        tick_duration_h=1 / 3600,
        delivery_intervals=(interval,),
        decision_interval_sec=30,
        projected_net_kwh=-0.5,
        contracted_net_kwh=-0.2,
        open_orders=[OpenOrderView(1, "buy", Decimal("-20"), Decimal("0.1"))],
        risk_rejections_total=2,
    )


def _valuation() -> ValuationSignal:
    return ValuationSignal(
        fair_buy_price=20,
        fair_sell_price=10,
        marginal_battery_value=15,
        battery_sell_price=16,
        battery_buy_price=14,
        surplus_kwh=0,
        deficit_kwh=0.5,
        soc_frac=0.5,
        soc_pressure=0,
    )


def test_v4_observation_is_signed_finite_and_carries_delivery_runtime_state():
    obs = encode_obs(_context(), _valuation(), obs_version=OBS_V4)
    assert obs.shape == (OBS_DIM_V4,)
    assert np.isfinite(obs).all()
    assert obs[7] < 0  # signed negative mid-price regime survives normalization
    assert obs[24] == pytest.approx(0.1)  # 30 s to gate / 300 s product
    assert obs[26] == pytest.approx(0.1)  # 30 s decision / 300 s product
    assert obs[29] == pytest.approx(0.02)  # two cumulative rejects / 100
    assert obs[31] == pytest.approx(-0.05)  # projected net / 10 kWh battery scale
    assert obs[32] == pytest.approx(-0.02)  # contracted net / same scale


def test_new_policy_and_env_defaults_use_v4_width():
    assert BCNet().net[0].in_features == OBS_DIM_V4
    env = VPPPrimitiveEnv({"seed": 1, "episode_ticks": 1})
    obs, _ = env.reset(seed=1)
    assert env.obs_version == OBS_V4
    assert obs.shape == (OBS_DIM_V4,)


def test_offline_env_uses_conserving_trade_ledger_and_finite_real_usd_reward():
    env = VPPPrimitiveEnv({"seed": 4, "episode_ticks": 1})
    env.reset(seed=4)
    _obs, reward, _terminated, _truncated, _info = env.step(
        np.zeros(env.action_space.shape, dtype=np.float32)
    )
    assert np.isfinite(reward)
    assert env._gateway.ledger.total(category=LedgerCategory.TRADE) == Decimal("0.000000")


def test_online_reward_uses_real_usd_inventory_and_residual_contract_exposure():
    prev = _Snap(
        pnl=0.0,
        pending=1.0,
        open_net=0.0,
        contracted_net=0.0,
        soc_frac=0.5,
        soc_kwh=10.0,
        rejections=0.0,
    )
    cur = _Snap(
        pnl=0.05,
        pending=1.0,
        open_net=0.0,
        contracted_net=1.0,
        soc_frac=0.5,
        soc_kwh=9.0,
        rejections=0.0,
    )
    # $0.05 realized exactly offsets marking one lost kWh at $50/MWh.
    assert compute_step_reward(prev, cur, RewardWeights(soc=0.0)) == pytest.approx(0.0)
