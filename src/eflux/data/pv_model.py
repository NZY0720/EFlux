"""PV physics via pvlib — converts hourly irradiance to AC kW output.

Minimal ModelChain wiring: tilted-plane irradiance via Hay-Davies → cell temp via
PVWatts model → DC via PVWatts → inverter via PVWatts. Good enough for sim PnL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass
class PVPhysicalModel:
    lat: float
    lon: float
    kw_peak: float
    tilt: float = 30.0       # degrees from horizontal
    azimuth: float = 180.0   # degrees clockwise from north (180 = south)
    # Cached weather DataFrame (set externally by simulator init).
    weather = None  # type: ignore[var-annotated]  # pd.DataFrame | None

    def output_kw(self, sim_ts: datetime) -> float:
        """Return AC power in kW at sim_ts. Falls back to 0 if no weather row."""
        if self.weather is None or self.weather.empty:
            return 0.0
        try:
            import numpy as np
            import pandas as pd
            import pvlib
        except ImportError:
            log.warning("pvlib not installed — PV physical model returning 0")
            return 0.0

        from eflux.data.weather import at_time

        row = at_time(self.weather, sim_ts)
        if row is None:
            return 0.0

        # Build a 1-row pandas Series of irradiance for pvlib.
        ts = pd.Timestamp(sim_ts).floor("h")
        location = pvlib.location.Location(self.lat, self.lon, tz="UTC")
        solpos = location.get_solarposition(pd.DatetimeIndex([ts]))
        ghi = float(row["ghi"]) if pd.notna(row["ghi"]) else 0.0
        dni = float(row["dni"]) if pd.notna(row["dni"]) else 0.0
        dhi = float(row["dhi"]) if pd.notna(row["dhi"]) else 0.0
        temp_air = float(row["temp_air"]) if pd.notna(row["temp_air"]) else 20.0
        wind_speed = float(row["wind_speed"]) if pd.notna(row["wind_speed"]) else 1.0

        # Plane-of-array irradiance.
        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=self.tilt,
            surface_azimuth=self.azimuth,
            solar_zenith=solpos["apparent_zenith"].iloc[0],
            solar_azimuth=solpos["azimuth"].iloc[0],
            dni=dni,
            ghi=ghi,
            dhi=dhi,
        )
        poa_global = float(poa.get("poa_global", 0.0) or 0.0)

        # PVWatts DC + AC (no DC/AC ratio cap for simplicity).
        # pdc0 in W = kw_peak * 1000.
        pdc0 = self.kw_peak * 1000.0
        cell_temp = pvlib.temperature.pvsyst_cell(
            poa_global=poa_global,
            temp_air=temp_air,
            wind_speed=wind_speed,
        )
        pdc = pvlib.pvsystem.pvwatts_dc(poa_global, cell_temp, pdc0, gamma_pdc=-0.004)
        pac = pvlib.inverter.pvwatts(pdc, pdc0=pdc0)
        # pac is a numpy scalar in watts; convert to kW and clip to non-negative.
        return max(0.0, float(np.asarray(pac).item()) / 1000.0)
