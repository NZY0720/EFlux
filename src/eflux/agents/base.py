"""Agent abstraction.

All agents (ZI / Truthful / PPO / Reflective) implement `decide()`. The simulator runner
calls this each tick. An agent owns a VPP, observes its own state + market snapshot, and
returns an AgentDecision that the runner submits through TradingGatewayV1.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from eflux.agents.decision import AgentDecision
from eflux.data.electricity_market import ExternalMarketQuote
from eflux.market.products import DeliveryInterval, next_delivery_interval
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad

if TYPE_CHECKING:
    from eflux.forecasting.schema import ForecastBundle


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
    # Canonical depth for behavior datasets and policies that need more than the
    # top of book. Values remain Decimal in-process and are serialized explicitly
    # at the dataset/audit boundary.
    bids: list[tuple[Decimal, Decimal]] = field(default_factory=list)
    asks: list[tuple[Decimal, Decimal]] = field(default_factory=list)
    interval_id: str | None = None
    delivery_start: datetime | None = None
    delivery_end: datetime | None = None
    gate_closure: datetime | None = None

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
            bids=[(Decimal(price), Decimal(qty)) for price, qty in snapshot.get("bids", ())],
            asks=[(Decimal(price), Decimal(qty)) for price, qty in snapshot.get("asks", ())],
            interval_id=snapshot.get("interval_id"),
            delivery_start=(
                datetime.fromisoformat(snapshot["delivery_start"])
                if snapshot.get("delivery_start")
                else None
            ),
            delivery_end=(
                datetime.fromisoformat(snapshot["delivery_end"])
                if snapshot.get("delivery_end")
                else None
            ),
            gate_closure=(
                datetime.fromisoformat(snapshot["gate_closure"])
                if snapshot.get("gate_closure")
                else None
            ),
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
    # Products visible to this decision. The first is the primary/nearest
    # delivery interval; policies may place orders farther along the horizon.
    delivery_intervals: tuple[DeliveryInterval, ...] = field(default_factory=tuple)
    # Built-in decision cadence is independent of the one-second physics tick.
    decision_interval_sec: float = 30.0
    # Forecast uncontrolled net injection for the primary interval. Positive is
    # surplus, negative is deficit. None is allowed in isolated policy tests.
    projected_net_kwh: float | None = None
    # Filled contractual net injection for the primary product (sell - buy).
    contracted_net_kwh: float = 0.0
    # Current dispatchable output, used to value startup decisions.
    dispatchable_power_kw: float = 0.0
    # Filled and resting dispatchable energy for the primary product. Gas agents
    # use these separately from aggregate net contracts so they only reoffer
    # residual nameplate capacity after partial fills.
    contracted_dispatchable_kwh: float = 0.0
    resting_dispatchable_kwh: float = 0.0
    # Signed energy this VPP has resting in non-dispatched book orders: sell
    # remainders positive, buy remainders negative — the same convention as
    # pending_net_kwh. Populated by the runner.
    open_orders_net_kwh: float = 0.0
    # This VPP's own resting orders (id, side, price, remaining qty, age). Empty
    # unless the runner populates it; strategy primitives that cancel/reprice
    # stale quotes read it. Defaulting to empty keeps existing agents/tests intact.
    open_orders: list[OpenOrderView] = field(default_factory=list)
    # Cumulative count of this VPP's gateway-rejected orders through the *previous*
    # tick (the runner reads its running tally when it builds the context, before
    # this tick's gating runs). An online learner takes the tick-to-tick delta as
    # the invalid-order reward penalty; default 0.0 keeps existing agents/tests intact.
    risk_rejections_total: float = 0.0
    # Cumulative absolute post-delivery imbalance (kWh) through the previous
    # settled interval.  Slow strategists use the delta as realized feedback.
    realized_imbalance_abs_kwh_total: float = 0.0
    # Runner-owned rolling silence histogram through the previous decision tick.
    # The mapping is shared read-only to avoid copying it on the hot path.
    silence_ticks: int = 0
    silence_reasons: Mapping[str, int] | None = None
    # Latest platform forecast bundle. Phase-A only transports this signal; agents
    # ignore it until later phases opt in.
    forecast: ForecastBundle | None = None

    @property
    def primary_interval(self) -> DeliveryInterval:
        if self.delivery_intervals:
            return self.delivery_intervals[0]
        return next_delivery_interval(self.state.sim_ts)


class BaseAgent(ABC):
    @abstractmethod
    def decide(self, ctx: AgentContext) -> AgentDecision: ...


class ExternalControlAgent(BaseAgent):
    """Physical VPP whose decisions arrive through the external protocol."""

    def decide(self, ctx: AgentContext) -> AgentDecision:
        return AgentDecision.hold("externally controlled participant")
