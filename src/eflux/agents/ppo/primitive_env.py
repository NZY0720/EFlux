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

import math
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
    ACTION_PROFILE_P2P,
    ENCODING_V1,
    OBS_V1,
    OBS_V3,
    action_profile_for_action_dim,
    action_profile_for_market,
    decode_action,
    encode_obs,
    encoding_version_for_action_dim,
    obs_dim_for,
    price_ref_scale,
)
from eflux.agents.ppo.primitive_encoding import (
    action_dim as encoding_action_dim,
)
from eflux.agents.strategy import OrderProgramCompiler
from eflux.agents.valuation import TruthfulValuationOracle
from eflux.forecasting.schema import ForecastBundle, ForecastPoint, TargetForecast
from eflux.market.matching_engine import MatchingEngine
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad

EPISODE_TICKS = 24
TICK_DURATION_H = 1.0
COUNTERPARTY_ORDERS_PER_TICK = 4
ORDER_TTL_TICKS = 3
VPP_ID = 1
COUNTERPARTY_ID = 999
# Fixed reference day for synthetic episodes — a deterministic (seed-offset) start so
# training/eval never depend on wall-clock time. Summer solstice = a strong solar signal.
_SYNTHETIC_EPOCH = datetime(2024, 6, 21, tzinfo=UTC)

# Reward weights (§7). Realized cash is the primary term; the rest shape behaviour.
W_INVENTORY = 0.1  # mark-to-market value of unsettled energy
W_IMBALANCE = 1.0  # unserved position
W_SOC = 8.0  # asymmetric deviation outside the SOC band
W_INVALID = 10.0  # per gate-vetoed order
W_DEGRADE = 0.3  # per kWh of battery throughput
W_EXCESS_ORDERS = 4.0  # per order beyond the soft cap
ORDER_SOFT_CAP = 3
SOC_LOW, SOC_HIGH = 0.1, 0.95


class VPPPrimitiveEnv(gym.Env):
    """Single-agent env over the structured StrategyAction space."""

    metadata: ClassVar[dict] = {"render_modes": []}

    def __init__(
        self,
        config: dict | None = None,
        *,
        encoding_version: int = ENCODING_V1,
        obs_version: int = OBS_V1,
        action_dim: int | None = None,
        action_profile: str | None = None,
    ) -> None:
        super().__init__()
        cfg = config or {}
        self._market_mode = str(cfg.get("market_mode", "p2p"))
        if "encoding_version" in cfg:
            encoding_version = int(cfg["encoding_version"])
        if "obs_version" in cfg:
            obs_version = int(cfg["obs_version"])
        if "action_dim" in cfg:
            action_dim = int(cfg["action_dim"])
        if "action_profile" in cfg:
            action_profile = str(cfg["action_profile"])
        if action_dim is not None:
            self.action_dim = int(action_dim)
            self.action_profile = action_profile or action_profile_for_action_dim(self.action_dim)
        else:
            self.action_profile = (
                action_profile or action_profile_for_market(self._market_mode) or ACTION_PROFILE_P2P
            )
            self.action_dim = encoding_action_dim(
                encoding_version, action_profile=self.action_profile
            )
        self.encoding_version = encoding_version_for_action_dim(self.action_dim)
        self.obs_version = int(obs_version)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(obs_dim_for(self.obs_version),), dtype=np.float32
        )
        # Loosely-bounded continuous action; decode_action() squashes/argmaxes it.
        self.action_space = spaces.Box(
            low=-5.0, high=5.0, shape=(self.action_dim,), dtype=np.float32
        )
        self._episode_ticks = int(cfg.get("episode_ticks", EPISODE_TICKS))
        self._seed = cfg.get("seed")
        self._params_pool: list[VPPParams] = cfg.get("params_pool") or [
            VPPParams(
                pv_kw_peak=8.0,
                battery_kwh=15.0,
                battery_kw_max=4.0,
                load_kw_base=4.0,
                markup_floor=0.4,
            ),
            VPPParams(
                pv_kw_peak=4.0,
                battery_kwh=20.0,
                battery_kw_max=6.0,
                load_kw_base=6.0,
                markup_floor=0.4,
            ),
            VPPParams(
                pv_kw_peak=2.0,
                battery_kwh=10.0,
                battery_kw_max=3.0,
                load_kw_base=3.0,
                markup_floor=0.4,
            ),
        ]
        # Optional RealMarketData (eflux.agents.ppo.training_data): when present the env
        # replays real CAISO price + real weather-driven PV instead of the synthetic walk.
        self._real_data = cfg.get("real_data")
        # Which market structure to train against: "p2p" = a noisy peer counter-party book;
        # "realprice" = a deep grid book at lmp ± fee (the agent is a pure price-taker, exactly
        # as in the live realprice market) → distinct obs/reward distribution → a per-market
        # checkpoint. Defaults to p2p for back-compat.
        self._txn_fee = float(cfg.get("transaction_fee", 2.0))
        self._forecast_noise_frac = float(cfg.get("forecast_noise_frac", 0.1))
        self._oracle = TruthfulValuationOracle(
            price_ref=Decimal(str(price_ref_scale())), demand_beta=0.5
        )
        self._compiler = OrderProgramCompiler()
        self._risk_gate = RiskGate()
        # Late-initialized in reset().
        self._rng: random.Random
        self._np_rng: np.random.Generator
        self._forecast_rng: np.random.Generator
        self._engine: MatchingEngine
        self._params: VPPParams
        self._state: VPPState
        self._pv: PV
        self._battery: Battery
        self._load: FlexibleLoad
        self._sim_ts: datetime
        self._tick = 0
        self._last_price_ref = price_ref_scale()

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        rseed = (
            seed
            if seed is not None
            else (self._seed if self._seed is not None else random.randint(0, 1 << 30))
        )
        self._rng = random.Random(rseed)
        self._np_rng = np.random.default_rng(rseed)
        self._forecast_rng = np.random.default_rng(rseed + 0xF03ECA57)
        self._rseed = rseed

        self._params = self._rng.choice(self._params_pool)
        self._sim_ts = self._pick_start()
        initial_soc = self._params.battery_kwh * self._params.battery_initial_soc_frac
        self._state = VPPState(sim_ts=self._sim_ts, soc_kwh=initial_soc)
        self._pv = PV(kw_peak=self._params.pv_kw_peak)
        self._battery = Battery(
            capacity_kwh=self._params.battery_kwh,
            max_power_kw=self._params.battery_kw_max,
            eta_rt=self._params.battery_eta_rt,
            soc_kwh=initial_soc,
        )
        self._load = FlexibleLoad(
            base_kw=self._params.load_kw_base, profile=self._params.load_profile
        )
        self._engine = MatchingEngine()
        self._tick = 0
        self._last_price_ref = price_ref_scale()
        self._oracle.reset()  # fresh gas-throttle cadence per episode

        self._step_der()
        self._seed_counterparty()
        return self._obs(), {}

    def step(self, action):
        self._apply_der_balance()
        scale = price_ref_scale()
        inv_start = (self._state.pending_net_kwh + self._battery.soc_kwh) * scale
        soc_throughput_start = self._battery.soc_kwh

        ctx = self._make_ctx()
        valuation = self._oracle.estimate(ctx)
        strategy_action = decode_action(
            action, version=self.encoding_version, action_profile=self.action_profile
        )
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
        inv_end = (self._state.pending_net_kwh + self._battery.soc_kwh) * scale
        open_net = self._open_orders_net()
        imbalance = abs(self._state.pending_net_kwh + open_net)
        soc = self._battery.soc_frac
        soc_dev = max(0.0, SOC_LOW - soc) + 0.25 * max(0.0, soc - SOC_HIGH)
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
        market = MarketSnapshot.from_engine(
            self._sim_ts,
            self._engine.snapshot(depth_levels=1),
            market_mode=self._market_mode,
            anchor_to_external=False,
        )
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
            forecast=self._make_forecast() if self.obs_version == OBS_V3 else None,
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
        return self._apply_fills(result.trades)

    def _apply_fills(self, trades) -> float:
        cash = 0.0
        for tr in trades:
            qty_f = float(tr.qty)
            party_is_buyer = tr.buy_vpp_id == VPP_ID
            amount = float(tr.price) * qty_f
            if party_is_buyer:
                cash -= amount
                cover = min(qty_f, max(0.0, -self._state.pending_net_kwh))
                self._state.pending_net_kwh += cover
                self._battery.apply_kwh(qty_f - cover)
                self._state.soc_kwh = self._battery.soc_kwh
            else:
                cash += amount
                clear = min(qty_f, max(0.0, self._state.pending_net_kwh))
                self._state.pending_net_kwh -= clear
                self._battery.apply_kwh(-(qty_f - clear))
                self._state.soc_kwh = self._battery.soc_kwh
        self._state.pnl += Decimal(str(cash))
        return cash

    def _expire_orders(self) -> None:
        self._engine.expire(sim_ts=self._sim_ts, wall_ts=self._sim_ts)

    def _apply_der_balance(self) -> None:
        cap = max(self._params.battery_kwh, 1.0)
        gen_kwh = self._state.net_kw * TICK_DURATION_H
        max_rate_kwh = max(0.0, self._battery.max_power_kw * TICK_DURATION_H)
        if gen_kwh >= 0.0:
            absorbed = min(
                gen_kwh, max(0.0, self._battery.capacity_kwh - self._battery.soc_kwh), max_rate_kwh
            )
            self._battery.apply_kwh(absorbed)
            self._state.pending_net_kwh += gen_kwh - absorbed
        else:
            needed = -gen_kwh
            supplied = min(needed, max(0.0, self._battery.soc_kwh), max_rate_kwh)
            self._battery.apply_kwh(-supplied)
            self._state.pending_net_kwh -= needed - supplied
        self._state.soc_kwh = self._battery.soc_kwh
        self._state.pending_net_kwh = min(cap, max(-cap, self._state.pending_net_kwh))

    def _open_order_count(self) -> int:
        return sum(
            1
            for side in ("buy", "sell")
            for o in self._engine.book.iter_orders(side)
            if o.vpp_id == VPP_ID
        )

    def _open_orders_net(self) -> float:
        net = 0.0
        for side in ("buy", "sell"):
            for o in self._engine.book.iter_orders(side):
                if o.vpp_id != VPP_ID or o.dispatched:
                    continue
                net += float(o.remaining_qty) if o.side == "sell" else -float(o.remaining_qty)
        return net

    def _pick_start(self) -> datetime:
        """Episode start time. Real-data mode samples a random window inside the fetched
        month (so episodes see diverse real price/weather). Synthetic mode uses a fixed
        reference day offset by the seed — reproducible (no wall-clock dependence) while
        still spanning different hours of day across episodes."""
        if self._real_data is None:
            return _SYNTHETIC_EPOCH + timedelta(hours=self._rseed % 24)
        span_h = max(1, self._real_data.hours - self._episode_ticks)
        offset = self._rng.randint(0, span_h)
        return (self._real_data.start + timedelta(hours=offset)).replace(
            minute=0, second=0, microsecond=0
        )

    def _step_der(self) -> None:
        self._state.sim_ts = self._sim_ts
        if self._real_data is not None:
            # Real irradiance drives PV: 1000 W/m² ≈ nameplate (small headroom for cool/clear).
            ghi = self._real_data.ghi_at(self._sim_ts)
            self._state.pv_kw = max(0.0, self._params.pv_kw_peak * min(1.2, ghi / 1000.0))
        else:
            self._state.pv_kw = self._pv.output_kw(self._sim_ts, self._rng)
        self._state.load_kw = self._load.draw_kw(self._sim_ts, self._rng)
        self._state.update_net()

    def _seed_counterparty(self) -> None:
        if self._real_data is not None:
            # Center the counter-party on the real LMP for this hour, so the agent trades
            # against the actual price curve (its own normalization scale stays fixed).
            ref = max(5.0, self._real_data.price_at(self._sim_ts))
            self._last_price_ref = ref
        else:
            drift = self._np_rng.normal(0.0, 1.0)
            revert = 0.05 * (price_ref_scale() - self._last_price_ref)
            self._last_price_ref = max(5.0, self._last_price_ref + drift + revert)
            ref = self._last_price_ref
        if self._market_mode == "realprice":
            self._seed_grid(ref)
        else:
            self._seed_peer_book(ref)

    def _seed_peer_book(self, ref: float) -> None:
        """p2p structure: a thin noisy two-sided peer book around the reference price, so the
        agent discovers price against peers and its own size can move the book."""
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

    def _seed_grid(self, ref: float) -> None:
        """realprice structure: a deep grid book at import/export (lmp ± fee). The agent is a
        pure price-taker — it can always buy at ref+fee or sell at ref-fee, and its volume
        never moves the price — mirroring the live realprice market's settlement."""
        depth = 1e6  # effectively unlimited grid liquidity → no price impact
        export = max(0.01, ref - self._txn_fee)  # grid bid the agent sells into
        import_ = max(export + 0.0002, ref + self._txn_fee)  # grid ask the agent buys from
        for side, price in (("buy", export), ("sell", import_)):
            try:
                self._engine.submit(
                    vpp_id=COUNTERPARTY_ID,
                    side=side,
                    price=Decimal(str(round(price, 4))),
                    qty=Decimal(str(depth)),
                    sim_ts=self._sim_ts,
                    wall_ts=self._sim_ts,
                    ttl_sec=TICK_DURATION_H * 3600.0 * ORDER_TTL_TICKS,
                )
            except ValueError:
                continue

    def _future_price_ref(self, ts: datetime) -> float:
        """Forecast future price from data the env already owns.

        Real-data episodes use the indexed historical LMP at the future hour. Synthetic
        episodes use the same mean-reverting process as `_seed_counterparty`, but take its
        conditional expectation from the current reference price instead of consuming random
        shocks, so V3 does not alter the episode RNG stream.
        """
        if self._real_data is not None:
            return max(5.0, self._real_data.price_at(ts, default=self._last_price_ref))
        hours = max(0, round((ts - self._sim_ts).total_seconds() / 3600.0))
        ref = self._last_price_ref
        target = price_ref_scale()
        for _ in range(hours):
            ref = max(5.0, ref + 0.05 * (target - ref))
        return ref

    def _future_ghi(self, ts: datetime) -> float:
        """Forecast future irradiance from real weather when available, else the synthetic
        clear-sky diurnal curve without the PV noise term."""
        if self._real_data is not None:
            return max(0.0, self._real_data.ghi_at(ts, default=0.0))
        hour = ts.hour + ts.minute / 60.0
        sun = math.sin(math.pi * (hour - 6) / 12) if 6 <= hour <= 18 else 0.0
        return max(0.0, 1000.0 * sun)

    def _forecast_noise(self, value: float) -> float:
        frac = max(0.0, self._forecast_noise_frac)
        if frac <= 0.0:
            return value
        return value * (1.0 + float(self._forecast_rng.normal(0.0, frac)))

    def _target_forecast(self, h1h: float, h12h: float) -> TargetForecast:
        return TargetForecast(
            h5m=ForecastPoint(float(h1h), 0.0),
            h1h=ForecastPoint(float(h1h), 0.0),
            h12h=ForecastPoint(float(h12h), 0.0),
        )

    def _make_forecast(self) -> ForecastBundle:
        ts_1h = self._sim_ts + timedelta(hours=1)
        ts_12h = self._sim_ts + timedelta(hours=12)
        real_1h = max(0.0, self._forecast_noise(self._future_price_ref(ts_1h)))
        real_12h = max(0.0, self._forecast_noise(self._future_price_ref(ts_12h)))
        p2p_1h = max(0.0, self._forecast_noise(self._future_price_ref(ts_1h)))
        p2p_12h = max(0.0, self._forecast_noise(self._future_price_ref(ts_12h)))
        ghi_1h = max(0.0, self._forecast_noise(self._future_ghi(ts_1h)))
        ghi_12h = max(0.0, self._forecast_noise(self._future_ghi(ts_12h)))
        zeros = self._target_forecast(0.0, 0.0)
        return ForecastBundle(
            as_of=self._sim_ts,
            model_version="primitive_env_known_future_v1",
            price_real=self._target_forecast(real_1h, real_12h),
            price_p2p=self._target_forecast(p2p_1h, p2p_12h),
            ghi=self._target_forecast(ghi_1h, ghi_12h),
            temp_air=zeros,
            wind_speed=zeros,
        )

    def _obs(self) -> np.ndarray:
        ctx = self._make_ctx()
        return encode_obs(ctx, self._oracle.estimate(ctx), obs_version=self.obs_version)

    def render(self):
        return None
