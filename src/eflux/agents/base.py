"""Agent abstraction.

All agents (ZI / Truthful / PPO / Reflective) implement `decide()`. The simulator runner
calls this each tick. An agent owns a VPP, observes its own state + market snapshot, and
returns a list of order intents that the runner submits to the matching engine.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import Battery, FlexibleLoad, PV


@dataclass
class OrderIntent:
    side: str  # "buy" or "sell"
    price: Decimal
    qty: Decimal


@dataclass
class MarketSnapshot:
    sim_ts: datetime
    best_bid: Decimal | None
    best_ask: Decimal | None
    last_price: Decimal | None
    mid_price: Decimal | None

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


class BaseAgent(ABC):
    @abstractmethod
    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        ...
