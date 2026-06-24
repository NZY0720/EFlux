"""Open-Meteo client — pulls hourly irradiance / temperature for a site.

Two endpoints, selected automatically per request:
- archive (`archive-api.open-meteo.com`) for fully historical ranges. The
  archive lags real-time by a few days, so it can never cover "now".
- forecast (`api.open-meteo.com/v1/forecast`) whenever the requested range
  touches today or the future — it serves recent past + 16 days ahead, which
  is what the live simulator (sim time ≈ wall time) actually needs.

Open-Meteo is free, no API key. Past days are cached per (lat, lon, date) as
parquet on disk; today/future days are never cached (the forecast still moves).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_FIELDS = ["shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation",
                 "temperature_2m", "wind_speed_10m"]


def _endpoint_for(end: date) -> str:
    """Archive can only serve fully-historical ranges; anything touching today
    or the future must come from the forecast endpoint."""
    return FORECAST_URL if end >= date.today() else ARCHIVE_URL


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
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }
    endpoint = _endpoint_for(end)
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        log.info("Fetching weather for lat=%.2f lon=%.2f %s..%s via %s", lat, lon, start, end, endpoint)
        resp = await client.get(endpoint, params=params)
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
    # Cache fully-past days so future single-day lookups are fast. Today and
    # future days come from a moving forecast — never cache those.
    if not df.empty:
        for d, group in df.groupby(df.index.date):  # type: ignore[union-attr]
            if d >= date.today():
                continue
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
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }
    endpoint = _endpoint_for(end)
    with httpx.Client(timeout=timeout_sec) as client:
        log.info("Fetching weather for lat=%.2f lon=%.2f %s..%s via %s", lat, lon, start, end, endpoint)
        resp = client.get(endpoint, params=params)
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
    # Cache fully-past days only (see fetch_hourly).
    if not df.empty:
        for d, group in df.groupby(df.index.date):  # type: ignore[union-attr]
            if d >= date.today():
                continue
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
