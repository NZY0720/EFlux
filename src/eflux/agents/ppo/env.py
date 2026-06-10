"""Single-agent Gymnasium environment for training the PPO VPP agent.

Design choices
--------------
- One PPO-controlled VPP trading against a *synthetic* counter-party (random orders
  around a slowly drifting reference price). This is the smallest setup that lets
  the agent see fills, reward, and SOC dynamics — much cheaper than embedding the
  full multi-agent Simulator. Trade-off: less realistic distribution of fills than
  prod, but plenty to validate that the interface, obs/action plumbing, and reward
  signal all work end-to-end.

- Observation (10-d Box): pv/load/SOC + cyclic hour + market microstructure.
- Action (3-d Box): [side_logit, price_offset_from_mid, qty_frac].
  * side: > 0 → buy, < 0 → sell, in [-0.1, 0.1] → no-op
  * price = mid * (1 + 0.5 * price_offset)  (so ±50% around mid)
  * qty = qty_frac * max_action_qty  (clipped to [0, max_action_qty])
- Reward: ΔPnL from trades this tick − SOC out-of-bounds penalty.
- Episode: 24 ticks (one synthetic day) by default.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from eflux.market.matching_engine import MatchingEngine
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import Battery, FlexibleLoad, PV

EPISODE_TICKS = 24
TICK_DURATION_H = 1.0  # one sim-hour per tick
PRICE_REF = 50.0  # reference electricity price in our toy market
COUNTERPARTY_ORDERS_PER_TICK = 4
MAX_ACTION_QTY = 5.0  # kWh per order
SOC_PENALTY = 1.0  # per kWh out of bounds


class VPPSingleAgentEnv(gym.Env):
    """Single-agent gym env training one VPP to trade against synthetic counter-orders."""

    metadata = {"render_modes": []}

    observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(10,), dtype=np.float32)
    # [side_logit, price_offset, qty_frac]
    action_space = spaces.Box(low=np.array([-1.0, -1.0, 0.0], dtype=np.float32),
                               high=np.array([1.0, 1.0, 1.0], dtype=np.float32), dtype=np.float32)

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        cfg = config or {}
        self._episode_ticks = int(cfg.get("episode_ticks", EPISODE_TICKS))
        self._seed = cfg.get("seed", None)
        self._params_pool: list[VPPParams] = cfg.get("params_pool") or [
            VPPParams(pv_kw_peak=8.0, battery_kwh=15.0, battery_kw_max=4.0, load_kw_base=1.2),
            VPPParams(pv_kw_peak=4.0, battery_kwh=30.0, battery_kw_max=8.0, load_kw_base=2.0),
            VPPParams(pv_kw_peak=2.0, battery_kwh=5.0, battery_kw_max=2.0, load_kw_base=4.0),
        ]
        # Late-initialized in reset().
        self._rng: random.Random
        self._np_rng: np.random.Generator
        self._engine: MatchingEngine
        self._params: VPPParams
        self._state: VPPState
        self._pv: PV
        self._battery: Battery
        self._load: FlexibleLoad
        self._sim_ts: datetime
        self._tick: int = 0
        self._last_price_ref: float = PRICE_REF

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        rseed = seed if seed is not None else (self._seed if self._seed is not None else random.randint(0, 1 << 30))
        self._rng = random.Random(rseed)
        self._np_rng = np.random.default_rng(rseed)

        self._params = self._rng.choice(self._params_pool)
        self._sim_ts = datetime.now(UTC).replace(microsecond=0)
        self._state = VPPState(sim_ts=self._sim_ts, soc_kwh=self._params.battery_kwh * 0.5)
        self._pv = PV(kw_peak=self._params.pv_kw_peak)
        self._battery = Battery(
            capacity_kwh=self._params.battery_kwh,
            max_power_kw=self._params.battery_kw_max,
            eta_rt=self._params.battery_eta_rt,
            soc_kwh=self._params.battery_kwh * 0.5,
        )
        self._load = FlexibleLoad(base_kw=self._params.load_kw_base)
        self._engine = MatchingEngine()
        self._tick = 0
        self._last_price_ref = PRICE_REF

        self._step_der()
        self._seed_counterparty()
        return self._obs(), {}

    def step(self, action):
        action = np.asarray(action, dtype=np.float32).flatten()
        side_logit, price_offset, qty_frac = float(action[0]), float(action[1]), float(action[2])

        snap = self._engine.snapshot(depth_levels=1)
        mid = self._mid_from_snap(snap)
        reward = 0.0

        # Decide whether to act.
        if abs(side_logit) >= 0.1:
            side = "buy" if side_logit > 0 else "sell"
            price = max(0.01, mid * (1.0 + 0.5 * max(-1.0, min(1.0, price_offset))))
            qty = max(0.0, min(1.0, qty_frac)) * MAX_ACTION_QTY
            if qty >= 0.01:
                try:
                    result = self._engine.submit(
                        vpp_id=1,
                        side=side,
                        price=Decimal(str(round(price, 4))),
                        qty=Decimal(str(round(qty, 4))),
                        sim_ts=self._sim_ts,
                        wall_ts=self._sim_ts,
                    )
                    # PnL from fills.
                    for tr in result.trades:
                        cash = float(tr.price) * float(tr.qty)
                        if side == "buy":
                            reward -= cash
                            self._battery.charge(
                                power_kw=float(tr.qty) / TICK_DURATION_H,
                                duration_h=TICK_DURATION_H,
                            )
                        else:
                            reward += cash
                            self._battery.discharge(
                                power_kw=float(tr.qty) / TICK_DURATION_H,
                                duration_h=TICK_DURATION_H,
                            )
                except ValueError:
                    reward -= 10.0  # invalid order penalty

        # SOC bounds penalty (battery already clips internally; this catches under-utilization).
        soc = self._battery.soc_kwh
        if soc < 0:
            reward -= SOC_PENALTY * (0 - soc)
        elif soc > self._battery.capacity_kwh:
            reward -= SOC_PENALTY * (soc - self._battery.capacity_kwh)

        # Advance to next tick.
        self._tick += 1
        self._sim_ts = self._sim_ts + timedelta(hours=TICK_DURATION_H)
        self._step_der()
        self._seed_counterparty()

        terminated = False
        truncated = self._tick >= self._episode_ticks
        return self._obs(), reward, terminated, truncated, {}

    # -- internals -----------------------------------------------------------

    def _step_der(self) -> None:
        self._state.sim_ts = self._sim_ts
        self._state.pv_kw = self._pv.output_kw(self._sim_ts, self._rng)
        self._state.load_kw = self._load.draw_kw(self._sim_ts, self._rng)
        self._state.update_net()

    def _seed_counterparty(self) -> None:
        """Inject random buy/sell orders around the drifting reference price."""
        # Random walk on the ref price (mean-reverting toward PRICE_REF).
        drift = self._np_rng.normal(0.0, 1.0)
        revert = 0.05 * (PRICE_REF - self._last_price_ref)
        self._last_price_ref = max(5.0, self._last_price_ref + drift + revert)
        ref = self._last_price_ref
        for _ in range(COUNTERPARTY_ORDERS_PER_TICK):
            side = self._rng.choice(["buy", "sell"])
            price_jitter = self._np_rng.normal(0.0, 0.05 * ref)
            price = max(0.01, ref + (price_jitter if side == "buy" else -price_jitter))
            qty = self._np_rng.uniform(0.05, 0.5)
            try:
                self._engine.submit(
                    vpp_id=999,  # arbitrary "market maker" id
                    side=side,
                    price=Decimal(str(round(price, 4))),
                    qty=Decimal(str(round(qty, 4))),
                    sim_ts=self._sim_ts,
                    wall_ts=self._sim_ts,
                )
            except ValueError:
                continue

    def _mid_from_snap(self, snap: dict) -> float:
        bb = snap.get("best_bid")
        ba = snap.get("best_ask")
        if bb and ba:
            return (float(bb) + float(ba)) / 2.0
        if bb:
            return float(bb)
        if ba:
            return float(ba)
        return self._last_price_ref

    def _obs(self) -> np.ndarray:
        snap = self._engine.snapshot(depth_levels=1)
        mid = self._mid_from_snap(snap)
        spread = 0.0
        bb = snap.get("best_bid")
        ba = snap.get("best_ask")
        if bb and ba:
            spread = (float(ba) - float(bb)) / max(mid, 1e-3)
        last = float(snap["last_price"]) if snap.get("last_price") else mid

        hour = self._sim_ts.hour + self._sim_ts.minute / 60.0
        return np.array(
            [
                self._state.pv_kw / max(self._params.pv_kw_peak, 1e-3),
                self._state.load_kw / max(self._params.load_kw_base * 2.0, 1e-3),
                self._battery.soc_frac,
                math.sin(2 * math.pi * hour / 24.0),
                math.cos(2 * math.pi * hour / 24.0),
                (float(bb) - mid) / max(mid, 1e-3) if bb else 0.0,
                (float(ba) - mid) / max(mid, 1e-3) if ba else 0.0,
                mid / PRICE_REF,
                spread,
                last / PRICE_REF,
            ],
            dtype=np.float32,
        )

    def render(self, mode: str = "human") -> Any:  # noqa: ARG002 — interface conformance
        return None
