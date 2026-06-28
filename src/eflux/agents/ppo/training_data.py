"""Real-world training data for the PPO policy.

Pulls ~1 month of real CAISO LMP (the market price the agent trades against) and the
matching Open-Meteo weather (which drives PV/wind generation), and exposes simple
hour-aligned lookups for the training env to replay. Both sources are the same ones the
live app already uses; weather is cached per-day by `data.weather`, and the CAISO price
series is cached to parquet per (node, window).

Synchronous by design — it runs off the event loop in the PPO training thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from eflux.config import PROJECT_ROOT, get_settings
from eflux.data.electricity_market import CaisoOasisClient

log = logging.getLogger(__name__)


@dataclass
class RealMarketData:
    """Hour-aligned real price + weather over [start, end] (UTC), with cheap lookups."""

    price: object  # pd.Series: hourly UTC ts -> LMP ($/MWh)
    weather: object  # pd.DataFrame: hourly UTC (ghi, dni, dhi, temp_air, wind_speed) at the PV site
    wind: object  # pd.DataFrame: same columns at the wind site
    start: datetime
    end: datetime

    @property
    def hours(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 3600))

    def _hour(self, ts: datetime) -> datetime:
        return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)

    def price_at(self, ts: datetime, default: float = 50.0) -> float:
        """Most recent LMP at or before `ts` (DAM is hourly; asof avoids gaps)."""
        if self.price is None or len(self.price) == 0:
            return default
        try:
            v = self.price.asof(self._hour(ts))
            return float(v) if v == v else default  # NaN guard
        except Exception:
            return default

    def ghi_at(self, ts: datetime, default: float = 0.0) -> float:
        return self._weather_field(self.weather, "ghi", ts, default)

    def wind_speed_at(self, ts: datetime, default: float = 0.0) -> float:
        return self._weather_field(self.wind, "wind_speed", ts, default)

    @staticmethod
    def _weather_field(df, col: str, ts: datetime, default: float) -> float:
        if df is None or getattr(df, "empty", True) or col not in getattr(df, "columns", []):
            return default
        target = ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        try:
            if target in df.index:
                v = df.loc[target, col]
                return float(v) if v == v else default
        except Exception:
            pass
        return default


def _price_cache_path(cache_dir: Path, node: str, start: date, end: date) -> Path:
    safe = node.replace("/", "_")
    return cache_dir / f"lmp_{safe}_{start.isoformat()}_{end.isoformat()}.parquet"


def load_real_market_data(
    *,
    days: int = 30,
    start_date: date | None = None,
    end_date: date | None = None,
    node: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    wind_lat: float | None = None,
    wind_lon: float | None = None,
    cache_dir: Path | None = None,
    refresh: bool = False,
) -> RealMarketData:
    """Fetch (or load from cache) real CAISO price + Open-Meteo weather.

    By default the window is the last `days` ending yesterday (UTC), so it is fully
    historical — CAISO DAM is published and the weather archive has settled.
    Backtests may pass explicit `start_date` / `end_date` so PPO warm-starts never
    use data from inside the evaluation window. PV/wind sites default to the
    configured market region's coordinates, matching the live simulator."""
    import pandas as pd  # local: optional 'data' extra

    settings = get_settings()
    node = node or settings.external_market_node
    lat = settings.site_default_lat if lat is None else lat
    lon = settings.site_default_lon if lon is None else lon
    wind_lat = settings.site_wind_lat if wind_lat is None else wind_lat
    wind_lon = settings.site_wind_lon if wind_lon is None else wind_lon

    if start_date is not None or end_date is not None:
        if start_date is None or end_date is None:
            raise ValueError("start_date and end_date must be provided together")
        start_d = start_date
        end_d = end_date
    else:
        end_d = date.today() - timedelta(days=1)
        start_d = end_d - timedelta(days=days)
    if start_d >= end_d:
        raise ValueError("start_date must be before end_date")
    start = datetime(start_d.year, start_d.month, start_d.day, tzinfo=UTC)
    end = datetime(end_d.year, end_d.month, end_d.day, tzinfo=UTC)

    cache_dir = cache_dir or (PROJECT_ROOT / "data" / "cache" / "training")
    cache_dir.mkdir(parents=True, exist_ok=True)

    # --- CAISO price (parquet cache per node+window) ---
    price_path = _price_cache_path(cache_dir, node, start_d, end_d)
    if price_path.exists() and not refresh:
        log.info("CAISO price cache hit: %s", price_path)
        price = pd.read_parquet(price_path)["lmp"]
    else:
        log.info("Fetching CAISO LMP history %s..%s for %s", start_d, end_d, node)
        rows = CaisoOasisClient().fetch_lmp_history_sync(node=node, start=start, end=end)
        price = pd.Series(
            {r.interval_start.astimezone(UTC).replace(minute=0, second=0, microsecond=0): float(r.price) for r in rows}
        ).sort_index()
        if len(price):
            try:
                price.rename("lmp").to_frame().to_parquet(price_path)
            except Exception:
                log.exception("CAISO price cache write failed: %s", price_path)
    log.info("CAISO price points: %d", len(price))

    # --- Weather (per-day parquet cache lives in data.weather) ---
    from eflux.data.weather import fetch_hourly_sync

    weather = fetch_hourly_sync(lat, lon, start_d, end_d)
    wind = fetch_hourly_sync(wind_lat, wind_lon, start_d, end_d)
    log.info("Weather rows: pv-site=%d wind-site=%d", len(weather), len(wind))

    return RealMarketData(price=price, weather=weather, wind=wind, start=start, end=end)
