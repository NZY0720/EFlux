"""Structured-action Gymnasium env (design note §7, Stage 1).

One PPO-controlled VPP acts in the *structured primitive space* — it emits a
`StrategyAction` (mode + parameters), which the real `OrderProgramCompiler` lowers and the
real `RiskGate` validates before it hits a matching engine — against a synthetic
counter-party. This trains the policy over the exact pipeline used live (oracle →
compiler → risk gate), so a checkpoint transfers to the live `PPOPrimitiveAgent` without
distribution shift in the action semantics.

Reward follows §7: realized cashflow + mark-to-market inventory minus imbalance, liquidity,
battery degradation, invalid-action, excessive-order, and SOC-target penalties.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.hybrid import RiskGate
from eflux.agents.ppo.primitive_encoding import (
    ACTION_DIM,
    OBS_DIM,
    PRICE_REF,
    decode_action,
    encode_obs,
)
from eflux.agents.strategy import OrderProgramCompiler
from eflux.agents.valuation import TruthfulValuationOracle
from eflux.market.matching_engine import MatchingEngine
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad

EPISODE_TICKS = 24
TICK_DURATION_H = 1.0
COUNTERPARTY_ORDERS_PER_TICK = 4
ORDER_TTL_TICKS = 3
VPP_ID = 1
COUNTERPARTY_ID = 999

# Reward weights (§7). Realized cash is the primary term; the rest shape behaviour.
W_INVENTORY = 0.1     # mark-to-market value of unsettled energy
W_IMBALANCE = 1.0     # unserved position
W_SOC = 15.0          # deviation outside the SOC band
W_INVALID = 10.0      # per gate-vetoed order
W_DEGRADE = 0.3       # per kWh of battery throughput
W_EXCESS_ORDERS = 4.0  # per order beyond the soft cap
ORDER_SOFT_CAP = 3
SOC_LOW, SOC_HIGH = 0.2, 0.8


class VPPPrimitiveEnv(gym.Env):
    """Single-agent env over the structured StrategyAction space."""

    metadata: ClassVar[dict] = {"render_modes": []}

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(OBS_DIM,), dtype=np.float32)
        # Loosely-bounded continuous action; decode_action() squashes/argmaxes it.
        self.action_space = spaces.Box(low=-5.0, high=5.0, shape=(ACTION_DIM,), dtype=np.float32)
        cfg = config or {}
        self._episode_ticks = int(cfg.get("episode_ticks", EPISODE_TICKS))
        self._seed = cfg.get("seed")
        self._params_pool: list[VPPParams] = cfg.get("params_pool") or [
            VPPParams(pv_kw_peak=8.0, battery_kwh=15.0, battery_kw_max=4.0, load_kw_base=4.0, markup_floor=0.4),
            VPPParams(pv_kw_peak=4.0, battery_kwh=20.0, battery_kw_max=6.0, load_kw_base=6.0, markup_floor=0.4),
            VPPParams(pv_kw_peak=2.0, battery_kwh=10.0, battery_kw_max=3.0, load_kw_base=3.0, markup_floor=0.4),
        ]
        self._oracle = TruthfulValuationOracle(price_ref=Decimal(str(PRICE_REF)), demand_beta=0.5)
        self._compiler = OrderProgramCompiler()
        self._risk_gate = RiskGate()
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
        self._tick = 0
        self._last_price_ref = PRICE_REF

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
        self._load = FlexibleLoad(base_kw=self._params.load_kw_base, profile=self._params.load_profile)
        self._engine = MatchingEngine()
        self._tick = 0
        self._last_price_ref = PRICE_REF

        self._step_der()
        self._seed_counterparty()
        return self._obs(), {}

    def step(self, action):
        cap = max(self._params.battery_kwh, 1.0)
        # Credit this tick's net energy into the untraded balance (DER accumulation).
        self._state.pending_net_kwh = min(cap, max(-cap, self._state.pending_net_kwh + self._state.net_kw * TICK_DURATION_H))
        inv_start = self._state.pending_net_kwh * PRICE_REF
        soc_throughput_start = self._battery.soc_kwh

        ctx = self._make_ctx()
        valuation = self._oracle.estimate(ctx)
        strategy_action = decode_action(action)
        compiled = self._compiler.compile(ctx, strategy_action, valuation)
        decision = self._risk_gate.validate(
            compiled.order_intents,
            vpp_id=VPP_ID,
            params=self._params,
            battery=self._battery,
            tick_h=TICK_DURATION_H,
            open_order_count=self._open_order_count(),
        )
        n_rejected = len(decision.rejected)

        realized = 0.0
        n_orders = 0
        for intent in decision.accepted:
            realized += self._submit(intent)
            n_orders += 1

        self._expire_orders()

        # Reward terms (§7).
        inv_end = self._state.pending_net_kwh * PRICE_REF
        open_net = self._open_orders_net()
        imbalance = abs(self._state.pending_net_kwh + open_net)
        soc = self._battery.soc_frac
        soc_dev = max(0.0, SOC_LOW - soc) + max(0.0, soc - SOC_HIGH)
        degrade = abs(self._battery.soc_kwh - soc_throughput_start)
        reward = (
            realized
            + W_INVENTORY * (inv_end - inv_start)
            - W_IMBALANCE * imbalance
            - W_SOC * soc_dev
            - W_INVALID * n_rejected
            - W_DEGRADE * degrade
            - W_EXCESS_ORDERS * max(0, n_orders - ORDER_SOFT_CAP)
        )

        self._tick += 1
        self._sim_ts = self._sim_ts + timedelta(hours=TICK_DURATION_H)
        self._step_der()
        self._seed_counterparty()

        truncated = self._tick >= self._episode_ticks
        return self._obs(), float(reward), False, truncated, {}

    # -- internals -----------------------------------------------------------

    def _make_ctx(self) -> AgentContext:
        market = MarketSnapshot.from_engine(self._sim_ts, self._engine.snapshot(depth_levels=1))
        return AgentContext(
            vpp_id=VPP_ID,
            params=self._params,
            state=self._state,
            pv=self._pv,
            battery=self._battery,
            load=self._load,
            market=market,
            rng=self._rng,
            tick_duration_h=TICK_DURATION_H,
            open_orders_net_kwh=self._open_orders_net(),
        )

    def _submit(self, intent) -> float:
        """Submit one accepted intent; mirror the runner's accounting for VPP_ID.
        Returns realized cash (sell positive, buy negative)."""
        ttl_sec = TICK_DURATION_H * 3600.0 * ORDER_TTL_TICKS
        try:
            result = self._engine.submit(
                vpp_id=VPP_ID,
                side=intent.side,
                price=intent.price,
                qty=intent.qty,
                sim_ts=self._sim_ts,
                wall_ts=self._sim_ts,
                ttl_sec=ttl_sec,
                dispatched=intent.dispatched,
            )
        except ValueError:
            return 0.0
        if not intent.dispatched:
            signed = -float(intent.qty) if intent.side == "sell" else float(intent.qty)
            self._state.pending_net_kwh += signed
        return self._apply_fills(result.trades)

    def _apply_fills(self, trades) -> float:
        cash = 0.0
        for tr in trades:
            qty_f = float(tr.qty)
            party_is_buyer = tr.buy_vpp_id == VPP_ID
            amount = float(tr.price) * qty_f
            if party_is_buyer:
                cash -= amount
                self._battery.charge(power_kw=qty_f / TICK_DURATION_H, duration_h=TICK_DURATION_H)
            else:
                cash += amount
                self._battery.discharge(power_kw=qty_f / TICK_DURATION_H, duration_h=TICK_DURATION_H)
        self._state.pnl += Decimal(str(cash))
        return cash

    def _expire_orders(self) -> None:
        cap = max(self._params.battery_kwh, 1.0)
        for order in self._engine.expire(sim_ts=self._sim_ts, wall_ts=self._sim_ts):
            if order.vpp_id != VPP_ID or order.dispatched:
                continue
            signed = float(order.remaining_qty) if order.side == "sell" else -float(order.remaining_qty)
            self._state.pending_net_kwh = min(cap, max(-cap, self._state.pending_net_kwh + signed))

    def _open_order_count(self) -> int:
        return sum(
            1 for side in ("buy", "sell") for o in self._engine.book.iter_orders(side) if o.vpp_id == VPP_ID
        )

    def _open_orders_net(self) -> float:
        net = 0.0
        for side in ("buy", "sell"):
            for o in self._engine.book.iter_orders(side):
                if o.vpp_id != VPP_ID or o.dispatched:
                    continue
                net += float(o.remaining_qty) if o.side == "sell" else -float(o.remaining_qty)
        return net

    def _step_der(self) -> None:
        self._state.sim_ts = self._sim_ts
        self._state.pv_kw = self._pv.output_kw(self._sim_ts, self._rng)
        self._state.load_kw = self._load.draw_kw(self._sim_ts, self._rng)
        self._state.update_net()

    def _seed_counterparty(self) -> None:
        drift = self._np_rng.normal(0.0, 1.0)
        revert = 0.05 * (PRICE_REF - self._last_price_ref)
        self._last_price_ref = max(5.0, self._last_price_ref + drift + revert)
        ref = self._last_price_ref
        for _ in range(COUNTERPARTY_ORDERS_PER_TICK):
            side = self._rng.choice(["buy", "sell"])
            jitter = self._np_rng.normal(0.0, 0.05 * ref)
            price = max(0.01, ref + (jitter if side == "buy" else -jitter))
            qty = self._np_rng.uniform(0.2, 1.0)
            try:
                self._engine.submit(
                    vpp_id=COUNTERPARTY_ID,
                    side=side,
                    price=Decimal(str(round(price, 4))),
                    qty=Decimal(str(round(qty, 4))),
                    sim_ts=self._sim_ts,
                    wall_ts=self._sim_ts,
                    ttl_sec=TICK_DURATION_H * 3600.0 * ORDER_TTL_TICKS,
                )
            except ValueError:
                continue

    def _obs(self) -> np.ndarray:
        return encode_obs(self._make_ctx(), self._oracle.estimate(self._make_ctx()))

    def render(self):
        return None
