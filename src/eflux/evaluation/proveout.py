"""Deterministic, cache-only historical replay for the private Prove-out product.

The managed strategy loop is deliberately chronological: a decision at hour ``t`` is
computed from the price prefix ending at ``t``. It neither regenerates forecasts nor
loads future realized data. The v1 ``battery_arbitrageur`` does not consume forecasts;
future strategies may consume only stored forecast vintages whose ``origin_ts <= t``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from eflux.config import PROJECT_ROOT, get_settings

CAISO_TZ = ZoneInfo("America/Los_Angeles")
PROVEOUT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "training"
MANAGED_PROVEOUT_STRATEGIES = frozenset({"battery_arbitrageur"})


class ProveOutDataError(RuntimeError):
    """The requested replay cannot be constructed from trustworthy cached data."""


@dataclass(frozen=True)
class PriceHour:
    timestamp: datetime
    price: float


def _cache_files(*, node: str | None = None, cache_dir: Path | None = None) -> list[Path]:
    node = node or get_settings().external_market_node
    cache_dir = cache_dir or PROVEOUT_CACHE_DIR
    safe = node.replace("/", "_")
    return sorted(cache_dir.glob(f"lmp_{safe}_*.parquet"))


def _cached_series(*, node: str | None = None, cache_dir: Path | None = None):
    """Read and de-duplicate cached CAISO LMPs without any network fallback."""
    import pandas as pd

    frames = []
    for path in _cache_files(node=node, cache_dir=cache_dir):
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            raise ProveOutDataError(f"cannot read cached CAISO prices: {path.name}") from exc
        if "lmp" in frame.columns:
            frames.append(frame[["lmp"]])
    if not frames:
        return pd.Series(dtype=float, name="lmp")
    series = pd.concat(frames).sort_index()["lmp"]
    series = series[~series.index.duplicated(keep="last")]
    if series.index.tz is None:
        series.index = series.index.tz_localize(UTC)
    else:
        series.index = series.index.tz_convert(UTC)
    return series.sort_index()


def _utc_bounds(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(start_date, time.min, tzinfo=CAISO_TZ).astimezone(UTC)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=CAISO_TZ).astimezone(
        UTC
    )
    return start, end


def _day_is_complete(series, day: date) -> bool:
    import pandas as pd

    start, end = _utc_bounds(day, day)
    expected = pd.date_range(start, end, freq="h", inclusive="left")
    values = series.reindex(expected)
    return len(values) == len(expected) and bool(values.notna().all())


def available_price_ranges(
    *, node: str | None = None, cache_dir: Path | None = None
) -> list[tuple[date, date]]:
    """Contiguous CAISO-local date ranges with every expected hourly LMP cached."""
    series = _cached_series(node=node, cache_dir=cache_dir)
    if series.empty:
        return []
    first = series.index.min().to_pydatetime().astimezone(CAISO_TZ).date()
    last = series.index.max().to_pydatetime().astimezone(CAISO_TZ).date()
    complete: list[date] = []
    day = first
    while day <= last:
        if _day_is_complete(series, day):
            complete.append(day)
        day += timedelta(days=1)

    ranges: list[tuple[date, date]] = []
    for day in complete:
        if not ranges or day != ranges[-1][1] + timedelta(days=1):
            ranges.append((day, day))
        else:
            ranges[-1] = (ranges[-1][0], day)
    return ranges


def format_available_ranges(ranges: list[tuple[date, date]]) -> str:
    if not ranges:
        return "none"
    return ", ".join(f"{start.isoformat()}..{end.isoformat()}" for start, end in ranges)


def window_is_available(
    start_date: date,
    end_date: date,
    *,
    ranges: list[tuple[date, date]] | None = None,
) -> bool:
    ranges = available_price_ranges() if ranges is None else ranges
    return any(start <= start_date and end_date <= end for start, end in ranges)


def load_cached_price_hours(
    start_date: date,
    end_date: date,
    *,
    node: str | None = None,
    cache_dir: Path | None = None,
) -> list[PriceHour]:
    """Load one inclusive local-date window and reject gaps instead of fabricating prices."""
    import pandas as pd

    series = _cached_series(node=node, cache_dir=cache_dir)
    start, end = _utc_bounds(start_date, end_date)
    expected = pd.date_range(start, end, freq="h", inclusive="left")
    selected = series.reindex(expected)
    if len(selected) != len(expected) or bool(selected.isna().any()):
        ranges = available_price_ranges(node=node, cache_dir=cache_dir)
        raise ProveOutDataError(
            "requested window is not fully cached; available ranges: "
            f"{format_available_ranges(ranges)}"
        )
    return [
        PriceHour(timestamp=ts.to_pydatetime(), price=float(value))
        for ts, value in selected.items()
    ]


def _solar_generation_mwh(timestamp: datetime, solar_mw: float) -> float:
    """Simple fixed clear-sky profile: sine from 06:00 to 18:00 CAISO local time.

    This intentionally avoids historical weather regeneration and therefore cannot leak
    realized future weather. One hourly MW sample is numerically MWh for that hour.
    """
    local = timestamp.astimezone(CAISO_TZ)
    hour = local.hour + local.minute / 60
    if hour < 6 or hour >= 18:
        return 0.0
    return solar_mw * math.sin(math.pi * (hour - 6) / 12)


def _battery_pf_by_day(
    prices: list[PriceHour], battery: dict[str, Any]
) -> dict[date, float]:
    """Modo TB-k analogue required by the Prove-out product contract.

    For every CAISO-local day, ``k = clamp(round(E/P), 1, 12)``. The i-th cheapest
    hour is paired with the i-th priciest. A pair counts only when the efficiency-
    adjusted spread is positive, and each counted pair-hour pays one power-hour of
    cycle cost. Pairing is an analytical report bound only; replay dispatch never sees it.
    """
    power = float(battery["power_mw"])
    energy = float(battery["energy_mwh"])
    eta = float(battery["round_trip_efficiency"])
    cycle_cost = float(battery.get("cycle_cost_per_mwh", 0.0))
    sqrt_eta = math.sqrt(eta)
    k = max(1, min(12, round(energy / power)))
    grouped: dict[date, list[float]] = defaultdict(list)
    for point in prices:
        grouped[point.timestamp.astimezone(CAISO_TZ).date()].append(point.price)

    result: dict[date, float] = {}
    for day, day_prices in grouped.items():
        cheapest = sorted(day_prices)[:k]
        priciest = sorted(day_prices, reverse=True)[:k]
        value = 0.0
        for charge_price, discharge_price in zip(cheapest, priciest, strict=True):
            adjusted_discharge = discharge_price * sqrt_eta
            adjusted_charge = charge_price / sqrt_eta
            if adjusted_discharge > adjusted_charge:
                value += power * (adjusted_discharge - adjusted_charge) - cycle_cost * power
        result[day] = value
    return result


def perfect_foresight_usd(
    prices: list[PriceHour], endowment: dict[str, Any]
) -> float:
    battery = endowment.get("battery")
    if battery:
        return sum(_battery_pf_by_day(prices, battery).values())
    solar_mw = float(endowment.get("solar_mw", 0.0))
    return sum(_solar_generation_mwh(point.timestamp, solar_mw) * point.price for point in prices)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _strategy_params(raw: dict[str, Any] | None) -> dict[str, float | int]:
    raw = raw or {}
    allowed = {
        "lookback_hours",
        "minimum_history_hours",
        "charge_percentile",
        "discharge_percentile",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unknown battery_arbitrageur params: {unknown}")
    lookback = int(raw.get("lookback_hours", 24))
    minimum = int(raw.get("minimum_history_hours", 4))
    charge = float(raw.get("charge_percentile", 0.25))
    discharge = float(raw.get("discharge_percentile", 0.75))
    if not 2 <= lookback <= 24 * 31:
        raise ValueError("lookback_hours must be in [2, 744]")
    if not 1 <= minimum <= lookback:
        raise ValueError("minimum_history_hours must be in [1, lookback_hours]")
    if not 0 <= charge < discharge <= 1:
        raise ValueError("charge_percentile must be below discharge_percentile in [0, 1]")
    return {
        "lookback_hours": lookback,
        "minimum_history_hours": minimum,
        "charge_percentile": charge,
        "discharge_percentile": discharge,
    }


def validate_strategy(strategy: dict[str, Any]) -> None:
    algorithm = str(strategy.get("algorithm", "battery_arbitrageur"))
    if algorithm not in MANAGED_PROVEOUT_STRATEGIES:
        choices = sorted(MANAGED_PROVEOUT_STRATEGIES)
        raise ValueError(f"unknown managed prove-out algorithm {algorithm!r}; choose from {choices}")
    _strategy_params(strategy.get("params"))


def _rounded(value: float) -> float:
    return round(value, 6)


def replay_price_hours(
    prices: list[PriceHour],
    endowment: dict[str, Any],
    strategy: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Replay an endowment over already-validated hourly prices."""
    algorithm = str(strategy.get("algorithm", "battery_arbitrageur"))
    if algorithm not in MANAGED_PROVEOUT_STRATEGIES:
        raise ValueError(f"unknown managed prove-out algorithm: {algorithm!r}")
    params = _strategy_params(strategy.get("params"))
    if not prices:
        raise ProveOutDataError("replay requires at least one cached price")

    battery = endowment.get("battery")
    solar_mw = float(endowment.get("solar_mw", 0.0))
    initial_cash = float(endowment.get("cash_usd", 10000.0))
    cash = initial_cash
    pnl = 0.0
    running_peak = 0.0
    max_drawdown = 0.0
    trades = 0
    risk_rejections = 0
    daily_pnl: dict[date, float] = defaultdict(float)
    baseline_by_day: dict[date, float] = defaultdict(float)

    for point in prices:
        day = point.timestamp.astimezone(CAISO_TZ).date()
        baseline_by_day[day] += _solar_generation_mwh(point.timestamp, solar_mw) * point.price

    if battery:
        power = float(battery["power_mw"])
        capacity = float(battery["energy_mwh"])
        sqrt_eta = math.sqrt(float(battery["round_trip_efficiency"]))
        cycle_cost = float(battery.get("cycle_cost_per_mwh", 0.0))
        soc = 0.0
        history: list[float] = []

        for point in prices:
            day = point.timestamp.astimezone(CAISO_TZ).date()
            # Point-in-time boundary: only the prefix through this observed hourly LMP
            # is present when thresholds and the action are computed.
            history.append(point.price)
            prefix = history[-int(params["lookback_hours"]) :]
            flow = 0.0
            if len(prefix) >= int(params["minimum_history_hours"]):
                low = _percentile(prefix, float(params["charge_percentile"]))
                high = _percentile(prefix, float(params["discharge_percentile"]))
                if point.price < low:
                    quantity = min(power, max(0.0, capacity - soc) / sqrt_eta)
                    if point.price > 0:
                        quantity = min(quantity, max(0.0, cash) / point.price)
                    if quantity <= 1e-12:
                        risk_rejections += 1
                    else:
                        soc += quantity * sqrt_eta
                        flow = -quantity * point.price
                        trades += 1
                elif point.price > high:
                    quantity = min(power, max(0.0, soc) * sqrt_eta)
                    if quantity <= 1e-12:
                        risk_rejections += 1
                    else:
                        soc -= quantity / sqrt_eta
                        flow = quantity * point.price - cycle_cost * quantity
                        trades += 1
            cash += flow
            pnl += flow
            daily_pnl[day] += flow
            running_peak = max(running_peak, pnl)
            max_drawdown = max(max_drawdown, running_peak - pnl)
        pf_by_day = _battery_pf_by_day(prices, battery)
    else:
        pf_by_day = dict(baseline_by_day)
        for point in prices:
            day = point.timestamp.astimezone(CAISO_TZ).date()
            flow = _solar_generation_mwh(point.timestamp, solar_mw) * point.price
            cash += flow
            pnl += flow
            daily_pnl[day] += flow
            if flow != 0:
                trades += 1
            running_peak = max(running_peak, pnl)
            max_drawdown = max(max_drawdown, running_peak - pnl)

    perfect = sum(pf_by_day.values())
    day_count = (end_date - start_date).days + 1
    normalizer_mw = float(battery["power_mw"]) if battery else solar_mw
    per_kw_month = (
        pnl * 30 / (normalizer_mw * 1000 * day_count) if normalizer_mw > 0 else 0.0
    )
    spread = 100 * pnl / perfect if perfect > 0 else None
    daily = []
    day = start_date
    while day <= end_date:
        day_pf = pf_by_day.get(day, 0.0)
        day_value = daily_pnl.get(day, 0.0)
        daily.append(
            {
                "date": day.isoformat(),
                "pnl_usd": _rounded(day_value),
                "spread_capture_pct": _rounded(100 * day_value / day_pf)
                if day_pf > 0
                else None,
            }
        )
        day += timedelta(days=1)

    return {
        "pnl_usd": _rounded(pnl),
        "per_kw_month": _rounded(per_kw_month),
        "spread_capture_pct": _rounded(spread) if spread is not None else None,
        "perfect_foresight_usd": _rounded(perfect),
        "baseline_hold_usd": _rounded(sum(baseline_by_day.values())),
        "max_drawdown_usd": _rounded(max_drawdown),
        "trades": trades,
        "risk_rejections": risk_rejections,
        "imbalance_penalty_usd": 0.0,
        "days": day_count,
        "daily": daily,
    }


def run_proveout(
    endowment: dict[str, Any],
    start_date: date,
    end_date: date,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    prices = load_cached_price_hours(start_date, end_date)
    return replay_price_hours(
        prices,
        endowment,
        strategy,
        start_date=start_date,
        end_date=end_date,
    )
