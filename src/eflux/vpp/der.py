"""Distributed Energy Resources: PV, Wind, Battery, Flexible Load.

PV supports two backends:
  - default `physical_model=None`: deterministic diurnal sine + noise (stub).
  - `physical_model=PVPhysicalModel(...)`: real irradiance via Open-Meteo + pvlib.

WindTurbine likewise: an AR(1) gust model around a mean speed by default, or
around the real hourly wind speed when an Open-Meteo weather DataFrame is
attached (the PV weather fetch already carries wind_speed_10m).

FlexibleLoad and Battery remain analytic — ResStock integration is future work.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eflux.data.pv_model import PVPhysicalModel

log = logging.getLogger(__name__)


@dataclass
class PV:
    kw_peak: float
    noise_std: float = 0.1
    physical_model: "PVPhysicalModel | None" = field(default=None, repr=False)
    _fallback_warned: bool = field(default=False, repr=False)

    def output_kw(self, sim_ts: datetime, rng: random.Random) -> float:
        """Return AC output in kW. Uses pvlib physics if a model is attached, else stub."""
        if self.physical_model is not None:
            try:
                weather = getattr(self.physical_model, "weather", None)
                if weather is not None and not getattr(weather, "empty", False):
                    target = sim_ts.replace(minute=0, second=0, microsecond=0)
                    if target not in getattr(weather, "index", []):
                        raise KeyError(target)
                    return self.physical_model.output_kw(sim_ts)
                raise ValueError("physical model has no weather attached")
            except Exception as e:
                # Don't crash the simulator tick on bad data — but say which
                # model degraded, once, so silent fallbacks are visible in logs.
                if not self._fallback_warned:
                    self._fallback_warned = True
                    log.warning(
                        "PV physical model (%.1f kWp) fell back to the sine stub: %s: %s",
                        self.kw_peak, type(e).__name__, e,
                    )
        hour = sim_ts.hour + sim_ts.minute / 60.0
        if 6 <= hour <= 18:
            sun = math.sin(math.pi * (hour - 6) / 12)
        else:
            sun = 0.0
        noisy = sun * (1.0 + rng.gauss(0.0, self.noise_std))
        return max(0.0, self.kw_peak * noisy)


@dataclass
class WindTurbine:
    rated_kw: float
    cut_in: float = 3.0       # m/s — below this the rotor doesn't turn
    rated_speed: float = 12.0  # m/s — at/above this output is rated_kw
    cut_out: float = 25.0     # m/s — storm shutdown
    mean_wind: float = 7.0    # m/s — stub base speed when no weather attached
    # Optional Open-Meteo weather DataFrame (UTC hourly, "wind_speed" column).
    weather = None  # type: ignore[var-annotated]  # pd.DataFrame | None
    _v: float | None = field(default=None, repr=False)  # AR(1) speed state

    def output_kw(self, sim_ts: datetime, rng: random.Random) -> float:
        """AC output in kW: hourly base speed (real or mean) + AR(1) gusts → power curve."""
        base = self.mean_wind
        if self.weather is not None and not getattr(self.weather, "empty", True):
            try:
                from eflux.data.weather import at_time

                row = at_time(self.weather, sim_ts)
                if row is not None:
                    v = float(row["wind_speed"])
                    if v == v:  # NaN guard
                        base = v
            except Exception:
                pass  # fall back to the stub base — never crash a tick
        if self._v is None:
            self._v = base
        # Slow mean reversion toward the hourly base + per-tick gust noise.
        self._v = 0.97 * self._v + 0.03 * base + rng.gauss(0.0, 0.15)
        self._v = max(0.0, self._v)
        return self._power_curve(self._v)

    def _power_curve(self, v: float) -> float:
        if v < self.cut_in or v >= self.cut_out:
            return 0.0
        if v >= self.rated_speed:
            return self.rated_kw
        frac = (v - self.cut_in) / (self.rated_speed - self.cut_in)
        return self.rated_kw * frac**3


@dataclass
class Battery:
    capacity_kwh: float
    max_power_kw: float
    eta_rt: float = 0.9  # round-trip efficiency
    soc_kwh: float = 0.0

    def charge(self, power_kw: float, duration_h: float) -> float:
        """Charge at given power for duration. Returns actual kWh stored."""
        if power_kw <= 0:
            return 0.0
        p = min(power_kw, self.max_power_kw)
        energy_in = p * duration_h
        eta_c = math.sqrt(self.eta_rt)
        stored = energy_in * eta_c
        room = self.capacity_kwh - self.soc_kwh
        stored = min(stored, room)
        self.soc_kwh += stored
        return stored

    def discharge(self, power_kw: float, duration_h: float) -> float:
        """Discharge at given power for duration. Returns actual kWh delivered."""
        if power_kw <= 0:
            return 0.0
        p = min(power_kw, self.max_power_kw)
        energy_out_request = p * duration_h
        eta_d = math.sqrt(self.eta_rt)
        # Energy drawn from cell to deliver `energy_out_request` at terminals:
        draw = energy_out_request / eta_d
        draw = min(draw, self.soc_kwh)
        delivered = draw * eta_d
        self.soc_kwh -= draw
        return delivered

    @property
    def soc_frac(self) -> float:
        return self.soc_kwh / self.capacity_kwh if self.capacity_kwh > 0 else 0.0


@dataclass
class FlexibleLoad:
    base_kw: float
    elasticity: float = 0.2  # ±fraction around base
    noise_std: float = 0.05
    # Daily shape: "residential" (morning+evening peaks), "industrial"
    # (workday shift, weekend slowdown), "commercial" (business hours),
    # "flat" (24/7 — datacenters, cold storage, plant auxiliaries).
    profile: str = "residential"

    def draw_kw(self, sim_ts: datetime, rng: random.Random) -> float:
        """Stub daily profiles. To be replaced by ResStock data."""
        hour = sim_ts.hour + sim_ts.minute / 60.0
        if self.profile == "industrial":
            if 8 <= hour <= 18:
                shape = 1.0  # main shift
            elif 6 <= hour < 8 or 18 < hour <= 20:
                shape = 0.6  # ramp-up / wind-down
            else:
                shape = 0.35  # night crew + standby systems
            if sim_ts.weekday() >= 5:
                shape *= 0.45  # weekend slowdown
        elif self.profile == "commercial":
            shape = 1.0 if 9 <= hour <= 21 else 0.25
        elif self.profile == "flat":
            shape = 1.0
        else:  # residential
            # Two peaks: 7-9 morning, 18-22 evening.
            morning = math.exp(-0.5 * ((hour - 8) / 1.5) ** 2)
            evening = math.exp(-0.5 * ((hour - 20) / 2.0) ** 2)
            shape = 0.4 + 0.4 * (morning + evening)
        noise = rng.gauss(0.0, self.noise_std)
        return max(0.0, self.base_kw * (shape + noise))
