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

from eflux.data.electricity_market import ExternalMarketQuote
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
class CancelIntent:
    """Cancel a resting order this VPP owns, by engine order id."""

    order_id: int


@dataclass
class ReplaceIntent:
    """Atomically reprice/resize a resting order this VPP owns. The runner may
    implement it as cancel-then-submit; the compiler emits it so a strategy can
    reprice stale quotes without a gap in the book."""

    order_id: int
    new_price: Decimal
    new_qty: Decimal


@dataclass
class OpenOrderView:
    """Read-only view of one of this VPP's own resting orders, surfaced to the
    agent so strategy primitives (e.g. CANCEL_REPRICE) can act on stale quotes.
    Populated by the runner from its open-order tracking."""

    order_id: int
    side: str
    price: Decimal
    remaining_qty: Decimal
    age_ticks: int = 0
    dispatched: bool = False


@dataclass
class MarketSnapshot:
    sim_ts: datetime
    best_bid: Decimal | None
    best_ask: Decimal | None
    last_price: Decimal | None
    mid_price: Decimal | None
    market_mode: str = "p2p"
    # Market-wide context for learning agents, populated once per tick by the
    # runner (empty for unit-test snapshots): recent fills with party names,
    # and each LLM agent's latest successful reflection (tagged with vpp_id so
    # an agent can filter itself out).
    recent_trades: list[dict] = field(default_factory=list)
    peer_reflections: list[dict] = field(default_factory=list)
    external_market: ExternalMarketQuote | None = None
    # Whether cost-based agents (truthful/ZI) should cap/floor their fair prices to
    # the live CAISO anchor. Both live markets set this False: P2P treats CAISO as a
    # reference line only (free price discovery), and the real-price market would
    # never cross the grid spread if valuations were pinned to the lmp. Defaults True
    # so unit-test snapshots keep the legacy anchoring behavior.
    anchor_to_external: bool = True

    @classmethod
    def from_engine(
        cls,
        sim_ts: datetime,
        snapshot: dict,
        *,
        external_market: ExternalMarketQuote | None = None,
        anchor_to_external: bool = True,
        market_mode: str = "p2p",
    ) -> MarketSnapshot:
        bb = Decimal(snapshot["best_bid"]) if snapshot.get("best_bid") else None
        ba = Decimal(snapshot["best_ask"]) if snapshot.get("best_ask") else None
        last = Decimal(snapshot["last_price"]) if snapshot.get("last_price") else None
        mid = (bb + ba) / 2 if (bb is not None and ba is not None) else last
        return cls(
            sim_ts=sim_ts,
            best_bid=bb,
            best_ask=ba,
            last_price=last,
            mid_price=mid,
            market_mode=market_mode,
            external_market=external_market,
            anchor_to_external=anchor_to_external,
        )


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
    # This VPP's own resting orders (id, side, price, remaining qty, age). Empty
    # unless the runner populates it; strategy primitives that cancel/reprice
    # stale quotes read it. Defaulting to empty keeps existing agents/tests intact.
    open_orders: list[OpenOrderView] = field(default_factory=list)
    # Cumulative count of this VPP's RiskGate-vetoed orders through the *previous*
    # tick (the runner reads its running tally when it builds the context, before
    # this tick's gating runs). An online learner takes the tick-to-tick delta as
    # the invalid-order reward penalty; default 0.0 keeps existing agents/tests intact.
    risk_rejections_total: float = 0.0


class BaseAgent(ABC):
    @abstractmethod
    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        ...
