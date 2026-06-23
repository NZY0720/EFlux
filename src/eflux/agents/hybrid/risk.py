"""RiskGate — the single hard-constraint authority.

Every order, from any source (built-in agent, learned PPO policy, Truthful fallback, or
external SDK/REST submission), passes through one gate before reaching the matching
engine (design note §5.4, principle #7). It has final veto power; neither LLM nor PPO may
bypass it.

Enforced here:
- price band and positivity
- quantity band (no dust, no fat-finger)
- optional notional cap
- max open orders per VPP and max new orders per tick (anti-spam)
- battery SOC feasibility for battery-backed dispatched orders

Defaults are deliberately generous — calibrated above the built-in roster's actual
ranges (prices ≲80 vs cap 1000, quantities ≲30 vs cap 1000) so the gate is a backstop for
pathological/learned actions, not a filter that silently reshapes the existing market.
Every rejection is logged for audit.

The gate does not enforce VPP ownership: that is resolved against the DB at the REST
layer (and is implicit for built-in agents, which only ever submit their own orders).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal

from eflux.agents.base import OrderIntent
from eflux.vpp.base import VPPParams
from eflux.vpp.der import Battery

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskLimits:
    price_min: Decimal = Decimal("0.0001")  # matching engine rejects <= 0; Truthful floor clamps here
    price_max: Decimal = Decimal("1000")  # matches the external-order Pydantic cap
    qty_min: Decimal = Decimal("0.01")  # the no-dust threshold shared with agents
    qty_max: Decimal = Decimal("1000")
    max_notional: Decimal | None = None  # price*qty cap; disabled by default
    # Resting orders per VPP. A built-in agent requotes its accumulated balance as
    # it re-crosses min_qty, and each quote rests until TTL expiry, so one VPP
    # legitimately holds up to ~order_ttl_sec/tick_sec resting orders in steady
    # state. The Simulator derives a TTL-aware value above that natural ceiling;
    # this standalone default is sized for the default 180s TTL / 1s tick.
    max_open_orders: int = 256
    max_new_orders_per_tick: int = 20
    # Reject battery-backed dispatched orders that exceed the battery's deliverable /
    # chargeable energy. Skipped for gas VPPs (their dispatched orders settle through
    # fuel, not storage — identified by gas_kw_max > 0).
    enforce_soc: bool = True


@dataclass(frozen=True)
class RejectedOrder:
    intent: OrderIntent
    reason: str


@dataclass
class RiskDecision:
    accepted: list[OrderIntent] = field(default_factory=list)
    rejected: list[RejectedOrder] = field(default_factory=list)

    @property
    def requires_fallback(self) -> bool:
        """The policy wanted to act but every order was vetoed — a safe fallback
        action should be tried in its place."""
        return bool(self.rejected) and not self.accepted


class RiskRejected(Exception):
    """Raised on the external-order path when the gate vetoes a submission."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RiskGate:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def validate(
        self,
        intents: list[OrderIntent],
        *,
        vpp_id: int,
        params: VPPParams | None = None,
        battery: Battery | None = None,
        tick_h: float = 1.0,
        open_order_count: int = 0,
    ) -> RiskDecision:
        """Validate a VPP's order batch against the hard limits. Orders are checked
        in order; accepting one consumes budget (open-order slots, battery headroom)
        for the next, so a batch can't collectively over-commit."""
        decision = RiskDecision()
        lim = self.limits
        new_resting = 0

        # Battery headroom is consumed across the batch (only for battery VPPs).
        is_gas = bool(params is not None and params.gas_kw_max > 0)
        track_soc = lim.enforce_soc and battery is not None and not is_gas
        soc_kwh = battery.soc_kwh if battery is not None else 0.0
        room_kwh = (battery.capacity_kwh - battery.soc_kwh) if battery is not None else 0.0
        sqrt_eta = math.sqrt(max(0.01, battery.eta_rt)) if battery is not None else 1.0

        for intent in intents:
            reason = self._reject_reason(
                intent, lim, new_resting, open_order_count, track_soc, soc_kwh, room_kwh, sqrt_eta
            )
            if reason is not None:
                decision.rejected.append(RejectedOrder(intent, reason))
                log.warning(
                    "RiskGate vetoed vpp=%s %s qty=%s @ %s: %s",
                    vpp_id, intent.side, intent.qty, intent.price, reason,
                )
                continue
            decision.accepted.append(intent)
            new_resting += 1
            if track_soc and intent.dispatched:
                # Consume the physical budget this order would use on fill.
                if intent.side == "sell":
                    soc_kwh -= float(intent.qty) / sqrt_eta  # cell energy drawn
                else:
                    room_kwh -= float(intent.qty) * sqrt_eta  # cell energy stored
        return decision

    def _reject_reason(
        self,
        intent: OrderIntent,
        lim: RiskLimits,
        new_resting: int,
        open_order_count: int,
        track_soc: bool,
        soc_kwh: float,
        room_kwh: float,
        sqrt_eta: float,
    ) -> str | None:
        price, qty = intent.price, intent.qty
        if not price.is_finite() or not qty.is_finite():
            return "non-finite price/qty"
        if price <= 0:
            return "price <= 0"
        if qty <= 0:
            return "qty <= 0"
        if price < lim.price_min:
            return f"price {price} below min {lim.price_min}"
        if price > lim.price_max:
            return f"price {price} above max {lim.price_max}"
        if qty < lim.qty_min:
            return f"qty {qty} below min {lim.qty_min}"
        if qty > lim.qty_max:
            return f"qty {qty} above max {lim.qty_max}"
        if lim.max_notional is not None and price * qty > lim.max_notional:
            return f"notional {price * qty} above max {lim.max_notional}"
        if new_resting >= lim.max_new_orders_per_tick:
            return f"exceeds {lim.max_new_orders_per_tick} new orders this tick"
        if open_order_count + new_resting >= lim.max_open_orders:
            return f"exceeds {lim.max_open_orders} open orders"
        if track_soc and intent.dispatched:
            tol = 1e-9
            if intent.side == "sell":
                deliverable = max(0.0, soc_kwh) * sqrt_eta
                if float(qty) > deliverable + tol:
                    return f"battery discharge {qty} exceeds deliverable {deliverable:.4f} kWh"
            else:
                chargeable = max(0.0, room_kwh) / sqrt_eta
                if float(qty) > chargeable + tol:
                    return f"battery charge {qty} exceeds chargeable {chargeable:.4f} kWh"
        return None
