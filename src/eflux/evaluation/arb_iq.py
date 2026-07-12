"""Battery-arbitrage profit and perfect-foresight spread capture.

Prices are USD/MWh and energy is kWh, so cash flows divide by 1,000.  The
oracle finishes at its starting SOC: initial inventory and inventory left at
the end of the window are therefore not counted as arbitrage profit.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np


def oracle_arb_profit(
    prices: Iterable[float],
    battery_kwh: float,
    battery_kw_max: float,
    interval_h: float,
    round_trip_eff: float,
    start_soc: float = 0.5,
) -> float:
    """Return the maximum realizable battery-arbitrage profit in USD.

    Dynamic programming uses 21 evenly spaced SOC levels.  Charge and
    discharge efficiencies are split symmetrically as ``sqrt(eta)`` and power
    in either direction is bounded by ``battery_kw_max``.
    """
    values = np.asarray(list(prices), dtype=float)
    inputs = (battery_kwh, battery_kw_max, interval_h, round_trip_eff, start_soc)
    if not all(math.isfinite(float(value)) for value in inputs):
        raise ValueError("battery inputs must be finite")
    if battery_kwh < 0 or battery_kw_max < 0:
        raise ValueError("battery capacity and power must be non-negative")
    if interval_h <= 0:
        raise ValueError("interval_h must be positive")
    if not 0 < round_trip_eff <= 1:
        raise ValueError("round_trip_eff must be in (0, 1]")
    if not 0 <= start_soc <= 1:
        raise ValueError("start_soc must be in [0, 1]")
    if not np.isfinite(values).all():
        raise ValueError("prices must be finite")
    if values.size == 0 or battery_kwh == 0 or battery_kw_max == 0:
        return 0.0

    # Include the exact initial SOC even if a caller chooses a value that is not
    # one of the regular 5% grid points, so the terminal constraint is exact.
    soc_grid = np.unique(np.append(np.linspace(0.0, battery_kwh, 21), start_soc * battery_kwh))
    start_idx = int(np.argmin(np.abs(soc_grid - start_soc * battery_kwh)))
    eta = math.sqrt(round_trip_eff)
    max_terminal_kwh = battery_kw_max * interval_h
    negative_inf = float("-inf")
    profit = np.full(len(soc_grid), negative_inf)
    profit[start_idx] = 0.0

    for price in values:
        next_profit = np.full(len(soc_grid), negative_inf)
        for from_idx, current in enumerate(profit):
            if not math.isfinite(float(current)):
                continue
            from_soc = float(soc_grid[from_idx])
            for to_idx, raw_to_soc in enumerate(soc_grid):
                to_soc = float(raw_to_soc)
                delta = to_soc - from_soc
                if delta >= 0:
                    grid_energy = delta / eta
                    if grid_energy > max_terminal_kwh + 1e-12:
                        continue
                    cash = -float(price) * grid_energy / 1000.0
                else:
                    grid_energy = -delta * eta
                    if grid_energy > max_terminal_kwh + 1e-12:
                        continue
                    cash = float(price) * grid_energy / 1000.0
                next_profit[to_idx] = max(next_profit[to_idx], current + cash)
        profit = next_profit

    result = float(profit[start_idx])
    return max(0.0, result) if math.isfinite(result) else 0.0


def _trade_value(trade: Any, *names: str) -> Any:
    if isinstance(trade, Mapping):
        for name in names:
            if name in trade:
                return trade[name]
    else:
        for name in names:
            if hasattr(trade, name):
                return getattr(trade, name)
    raise ValueError(f"trade is missing {names[0]}")


def realized_arb_profit(trades: Iterable[Any]) -> float:
    """FIFO-pair executed buys with later sells and return realized profit USD.

    Each trade may be a mapping or object with ``side``, ``price`` and either
    ``qty`` or ``quantity``.  Sells without previously bought inventory and
    buys left unmatched at the end are ignored.
    """
    inventory: deque[list[float]] = deque()
    profit = 0.0
    for trade in trades:
        side = str(_trade_value(trade, "side")).lower()
        price = float(_trade_value(trade, "price"))
        qty = float(_trade_value(trade, "qty", "quantity", "qty_kwh"))
        if side not in {"buy", "sell"}:
            raise ValueError("trade side must be 'buy' or 'sell'")
        if not math.isfinite(price) or not math.isfinite(qty) or qty < 0:
            raise ValueError(
                "trade price and quantity must be finite; quantity must be non-negative"
            )
        if side == "buy":
            if qty > 0:
                inventory.append([qty, price])
            continue

        remaining = qty
        while remaining > 1e-12 and inventory:
            bought_qty, bought_price = inventory[0]
            paired = min(remaining, bought_qty)
            profit += (price - bought_price) * paired / 1000.0
            remaining -= paired
            bought_qty -= paired
            if bought_qty <= 1e-12:
                inventory.popleft()
            else:
                inventory[0][0] = bought_qty
    return profit


def spread_capture(realized: float, oracle: float) -> float | None:
    """Return realized/oracle in [0, 1.5], or None when unavailable."""
    if realized is None or oracle is None:  # type: ignore[redundant-expr]
        return None
    realized_value = float(realized)
    oracle_value = float(oracle)
    if not math.isfinite(realized_value) or not math.isfinite(oracle_value) or oracle_value <= 0:
        return None
    return min(1.5, max(0.0, realized_value / oracle_value))
