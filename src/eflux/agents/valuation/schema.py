"""Valuation signal — what a VPP's energy is worth this tick.

A pure data record produced by `TruthfulValuationOracle`. It decouples *valuation*
(what the energy is economically worth) from *decision* (how to trade it): the
Truthful agent assembles orders from it, the strategy compiler prices primitives off
it, and the RiskGate uses it as an economic reference for sane price bands.

Prices are floats in the market's price unit (currency per kWh); quantities are kWh.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    # True when the surplus on offer is dispatchable generation (gas fuel) rather than
    # ambient/battery energy: its sell orders must carry dispatched=True so the book keeps
    # them out of ambient open-order exposure. Gas folds into the existing surplus_kwh /
    # fair_sell_price channels (so the PPO obs is unchanged); this flag only routes the
    # resulting orders. Default False keeps every non-gas path identical.
    supply_dispatched: bool = False
