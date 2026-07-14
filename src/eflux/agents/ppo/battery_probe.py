"""Zero-endowment price-spread competence probe for PPO checkpoints."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

import pandas as pd

from eflux.agents.ppo.bc import BCPolicy, checkpoint_meta, load_bc
from eflux.agents.ppo.primitive_encoding import encode_action, set_price_ref_scale
from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv
from eflux.agents.ppo.training_data import RealMarketData
from eflux.agents.strategy.schema import StrategyMode
from eflux.vpp.base import VPPParams


def probe_checkpoint(path: str, *, ticks: int = 288) -> dict:
    """Count physical battery orders over a deterministic low/high-price episode."""
    meta = checkpoint_meta(path)
    market_mode = str(meta.get("market_mode") or "p2p")
    action_profile = str(meta.get("action_profile") or market_mode)
    set_price_ref_scale(meta.get("price_ref"))
    policy = BCPolicy(
        load_bc(path),
        obs_version=int(meta["obs_version"]),
        action_profile=action_profile,
    )

    start = datetime(2026, 1, 1, tzinfo=UTC)
    index = pd.date_range(start, periods=72, freq="h")
    prices = pd.Series([10.0 if (hour // 6) % 2 == 0 else 80.0 for hour in range(72)], index=index)
    weather = pd.DataFrame({"ghi": 0.0, "wind_speed": 0.0}, index=index)
    data = RealMarketData(
        price=prices,
        weather=weather,
        wind=weather,
        start=start,
        end=start + timedelta(hours=72),
    )
    params = VPPParams(
        pv_kw_peak=0.0,
        wind_kw_rated=0.0,
        load_kw_base=0.0,
        battery_kwh=20.0,
        battery_kw_max=5.0,
        markup_floor=0.4,
    )
    env = VPPPrimitiveEnv(
        {
            "market_mode": market_mode,
            "real_data": data,
            "episode_ticks": ticks,
            "obs_version": int(meta["obs_version"]),
            "action_profile": action_profile,
        }
    )
    env.reset(seed=0, options={"params": params})
    battery_modes = (
        {StrategyMode.BATTERY_ARBITRAGE}
        if market_mode == "p2p"
        else {StrategyMode.GRID_CHARGE_ON_DIP, StrategyMode.GRID_DISCHARGE_ON_PEAK}
    )
    hit_count = 0
    modes: set[str] = set()
    refs: list[float] = []
    for _ in range(ticks):
        ctx = env._make_ctx()
        valuation = env._oracle.estimate(ctx)
        action = policy.select_action(ctx, valuation)
        program = env._compiler.compile(ctx, action, valuation)
        quantity = sum(float(order.qty_kwh) for order in program.order_requests)
        refs.append(env._last_price_ref)
        if action.mode in battery_modes and quantity > 0.0:
            hit_count += 1
            modes.add(action.mode.value)
        env.step(encode_action(action, action_profile=action_profile))
    return {
        "checkpoint": path,
        "market_mode": market_mode,
        "ticks": ticks,
        "price_min": min(refs),
        "price_max": max(refs),
        "nonzero_battery_order_ticks": hit_count,
        "battery_modes": sorted(modes),
        "pass": hit_count > 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--ticks", type=int, default=288)
    args = parser.parse_args()
    print(json.dumps(probe_checkpoint(args.checkpoint, ticks=args.ticks), sort_keys=True))


if __name__ == "__main__":
    main()
