"""Valuation signal — what a VPP's energy is worth this tick.

A pure data record produced by `TruthfulValuationOracle`. It decouples *valuation*
(what the energy is economically worth) from *decision* (how to trade it): the
Truthful agent assembles orders from it, the strategy compiler prices primitives off
it, and the RiskGate uses it as an economic reference for sane price bands.

Prices are floats in USD/MWh; quantities are terminal kWh.
"""

from __future__ import annotations

from dataclasses import dataclass

from eflux.market.delivery import OrderPurpose


@dataclass(frozen=True)
class ValuationSignal:
    # Marginal value of buying to cover the current deficit, including scarcity
    # pricing (rises with the unserved fraction up to the price cap).
    fair_buy_price: float
    # Marginal cost of selling the current surplus: pure-renewable surplus quotes
    # the floor; battery-sourced surplus quotes its delivery cost.
    fair_sell_price: float
    # Neutral fair value of energy held in the battery (midpoint of storage value
    # and delivery cost) — a reference for detecting irrational quotes.
    marginal_battery_value: float
    # Cost to deliver one kWh out of the battery: price_ref / sqrt(eta_rt).
    battery_sell_price: float
    # Value of storing one kWh into the battery: price_ref * sqrt(eta_rt).
    battery_buy_price: float
    # Accumulated untraded surplus / deficit (kWh); at most one is > 0.
    surplus_kwh: float
    deficit_kwh: float
    # Battery state: fraction of capacity (0..1), and signed pressure relative to a
    # neutral mid SOC (negative = room to charge, positive = should discharge).
    soc_frac: float
    soc_pressure: float
    # Physical resource backing the surplus. Gas folds into the existing
    # surplus/fair-price channels but is routed to dispatchable reservations.
    supply_purpose: OrderPurpose = OrderPurpose.BALANCE
    expected_ref_1h: float | None = None
    expected_ref_12h: float | None = None
    # Normalized forward price direction in roughly [-1, 1]; positive means the
    # selected market reference is expected to rise.
    price_trend: float = 0.0
