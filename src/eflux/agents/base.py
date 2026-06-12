"""Agent abstraction.

All agents (ZI / Truthful / PPO / Reflective) implement `decide()`. The simulator runner
calls this each tick. An agent owns a VPP, observes its own state + market snapshot, and
returns a list of order intents that the runner submits to the matching engine.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


@dataclass
class OrderIntent:
    side: str  # "buy" or "sell"
    price: Decimal
    qty: Decimal
    # True for dispatched energy (battery-band arbitrage, gas generation):
    # it settles through storage or fuel on fill, not through the ambient
    # renewable-load balance — the runner must not debit pending_net_kwh.
    dispatched: bool = False


@dataclass
class MarketSnapshot:
    sim_ts: datetime
    best_bid: Decimal | None
    best_ask: Decimal | None
    last_price: Decimal | None
    mid_price: Decimal | None
    # Market-wide context for learning agents, populated once per tick by the
    # runner (empty for unit-test snapshots): recent fills with party names,
    # and each LLM agent's latest successful reflection (tagged with vpp_id so
    # an agent can filter itself out).
    recent_trades: list[dict] = field(default_factory=list)
    peer_reflections: list[dict] = field(default_factory=list)

    @classmethod
    def from_engine(cls, sim_ts: datetime, snapshot: dict) -> MarketSnapshot:
        bb = Decimal(snapshot["best_bid"]) if snapshot.get("best_bid") else None
        ba = Decimal(snapshot["best_ask"]) if snapshot.get("best_ask") else None
        last = Decimal(snapshot["last_price"]) if snapshot.get("last_price") else None
        mid = (bb + ba) / 2 if (bb is not None and ba is not None) else last
        return cls(sim_ts=sim_ts, best_bid=bb, best_ask=ba, last_price=last, mid_price=mid)


@dataclass
class AgentContext:
    """Stuff each agent gets every tick to make decisions."""

    vpp_id: int
    params: VPPParams
    state: VPPState
    pv: PV
    battery: Battery
    load: FlexibleLoad
    market: MarketSnapshot
    rng: random.Random
    tick_duration_h: float
    # Signed energy this VPP has "spoken for" in resting (non-dispatched) book
    # orders: sell remainders positive, buy remainders negative — the same
    # convention as pending_net_kwh, which is debited at submit time. Together
    # `pending_net_kwh + open_orders_net_kwh` is the true unserved position;
    # without it a deficit agent sees only the post-debit sliver and can never
    # price scarcity (the demand_beta mechanism). Populated by the runner.
    open_orders_net_kwh: float = 0.0


class BaseAgent(ABC):
    @abstractmethod
    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        ...
