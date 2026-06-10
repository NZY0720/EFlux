"""Open-Meteo client — pulls historical hourly irradiance / temperature for a site.

We use the archive endpoint (`archive-api.open-meteo.com`) because the live
simulator's sim time is arbitrary — historical data covers all the periods we'd
want to replay. Open-Meteo is free, no API key.

Caches per (lat, lon, date) as parquet on disk so a re-run is instant.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_FIELDS = ["shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation",
                 "temperature_2m", "wind_speed_10m"]


def _cache_path(cache_dir: Path, lat: float, lon: float, day: date) -> Path:
    return cache_dir / f"weather_{lat:.2f}_{lon:.2f}_{day.isoformat()}.parquet"


async def fetch_hourly(
    lat: float,
    lon: float,
    start: date,
    end: date,
    *,
    cache_dir: Path | None = None,
    timeout_sec: float = 30.0,
):
    """Return a pandas.DataFrame indexed by UTC hourly timestamps over [start, end].

    Columns: ghi (W/m²), dni (W/m²), dhi (W/m²), temp_air (°C), wind_speed (m/s).
    """
    import pandas as pd  # local import: optional 'data' extra

    cache_dir = cache_dir or Path("data/cache/weather")
    cache_dir.mkdir(parents=True, exist_ok=True)

    # If a single-day cache covers the request, short-circuit.
    if start == end:
        path = _cache_path(cache_dir, lat, lon, start)
        if path.exists():
            log.debug("Weather cache hit: %s", path)
            return pd.read_parquet(path)

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": "UTC",
    }
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        log.info("Fetching weather for lat=%.2f lon=%.2f %s..%s", lat, lon, start, end)
        resp = await client.get(BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    hourly = data.get("hourly") or {}
    ts = pd.to_datetime(hourly.get("time", []), utc=True)
    df = pd.DataFrame(
        {
            "ghi": hourly.get("shortwave_radiation", []),
            "dni": hourly.get("direct_normal_irradiance", []),
            "dhi": hourly.get("diffuse_radiation", []),
            "temp_air": hourly.get("temperature_2m", []),
            "wind_speed": hourly.get("wind_speed_10m", []),
        },
        index=ts,
    )
    # Cache by day so future single-day lookups are fast.
    if not df.empty:
        for d, group in df.groupby(df.index.date):  # type: ignore[union-attr]
            try:
                group.to_parquet(_cache_path(cache_dir, lat, lon, d))
            except Exception:
                log.exception("Weather cache write failed for %s", d)
    return df


def fetch_hourly_sync(
    lat: float,
    lon: float,
    start: date,
    end: date,
    *,
    cache_dir: Path | None = None,
    timeout_sec: float = 30.0,
):
    """Sync flavor. Used from simulator init (which is itself called inside an async
    lifespan, so `asyncio.run` would error)."""
    import pandas as pd

    cache_dir = cache_dir or Path("data/cache/weather")
    cache_dir.mkdir(parents=True, exist_ok=True)

    if start == end:
        path = _cache_path(cache_dir, lat, lon, start)
        if path.exists():
            log.debug("Weather cache hit: %s", path)
            return pd.read_parquet(path)

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": "UTC",
    }
    with httpx.Client(timeout=timeout_sec) as client:
        log.info("Fetching weather for lat=%.2f lon=%.2f %s..%s", lat, lon, start, end)
        resp = client.get(BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    hourly = data.get("hourly") or {}
    ts = pd.to_datetime(hourly.get("time", []), utc=True)
    df = pd.DataFrame(
        {
            "ghi": hourly.get("shortwave_radiation", []),
            "dni": hourly.get("direct_normal_irradiance", []),
            "dhi": hourly.get("diffuse_radiation", []),
            "temp_air": hourly.get("temperature_2m", []),
            "wind_speed": hourly.get("wind_speed_10m", []),
        },
        index=ts,
    )
    if not df.empty:
        for d, group in df.groupby(df.index.date):  # type: ignore[union-attr]
            try:
                group.to_parquet(_cache_path(cache_dir, lat, lon, d))
            except Exception:
                log.exception("Weather cache write failed for %s", d)
    return df


def at_time(df, ts: datetime):
    """Return the row matching `ts` rounded down to the hour, or None if missing."""
    if df.empty:
        return None
    target = ts.replace(minute=0, second=0, microsecond=0)
    if target not in df.index:
        return None
    return df.loc[target]
