"""PPOAgent — plugs a trained policy into the live simulator.

The same observation layout used in training (eflux.agents.ppo.env) is reproduced
here from the live AgentContext so the policy gets inputs in the distribution it
was trained on.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from eflux.agents.base import AgentContext, BaseAgent, OrderIntent
from eflux.agents.ppo.env import MAX_ACTION_QTY, PRICE_REF
from eflux.agents.ppo.policy import PPOPolicyWrapper

log = logging.getLogger(__name__)


@dataclass
class PPOAgent(BaseAgent):
    checkpoint_path: str
    min_qty: Decimal = Decimal("0.01")
    _policy: PPOPolicyWrapper = field(init=False)

    def __post_init__(self) -> None:
        self._policy = PPOPolicyWrapper(self.checkpoint_path)

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        obs = self._make_obs(ctx)
        try:
            action = self._policy.act(obs)
        except Exception:
            log.exception("PPO policy inference failed; emitting no order")
            return []

        side_logit, price_offset, qty_frac = float(action[0]), float(action[1]), float(action[2])
        if abs(side_logit) < 0.1:
            return []

        side = "buy" if side_logit > 0 else "sell"
        mid = self._mid_from_market(ctx) or PRICE_REF
        price_f = max(0.01, mid * (1.0 + 0.5 * max(-1.0, min(1.0, price_offset))))
        qty_f = max(0.0, min(1.0, qty_frac)) * MAX_ACTION_QTY
        price = Decimal(str(round(price_f, 4)))
        qty = Decimal(str(round(qty_f, 4)))
        if qty < self.min_qty:
            return []
        return [OrderIntent(side=side, price=price, qty=qty)]

    @staticmethod
    def _mid_from_market(ctx: AgentContext) -> float | None:
        m = ctx.market
        if m.mid_price is not None:
            return float(m.mid_price)
        if m.last_price is not None:
            return float(m.last_price)
        return None

    @staticmethod
    def _make_obs(ctx: AgentContext) -> np.ndarray:
        m = ctx.market
        mid = PPOAgent._mid_from_market(ctx) or PRICE_REF
        bb = float(m.best_bid) if m.best_bid is not None else 0.0
        ba = float(m.best_ask) if m.best_ask is not None else 0.0
        spread = ((ba - bb) / max(mid, 1e-3)) if (m.best_bid is not None and m.best_ask is not None) else 0.0
        last = float(m.last_price) if m.last_price is not None else mid
        hour = ctx.state.sim_ts.hour + ctx.state.sim_ts.minute / 60.0
        return np.array(
            [
                ctx.state.pv_kw / max(ctx.params.pv_kw_peak, 1e-3),
                ctx.state.load_kw / max(ctx.params.load_kw_base * 2.0, 1e-3),
                ctx.battery.soc_frac,
                math.sin(2 * math.pi * hour / 24.0),
                math.cos(2 * math.pi * hour / 24.0),
                (bb - mid) / max(mid, 1e-3) if m.best_bid is not None else 0.0,
                (ba - mid) / max(mid, 1e-3) if m.best_ask is not None else 0.0,
                mid / PRICE_REF,
                spread,
                last / PRICE_REF,
            ],
            dtype=np.float32,
        )
