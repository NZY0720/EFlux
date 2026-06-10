"""Distributed Energy Resources: PV, Battery, Flexible Load.

PV supports two backends:
  - default `physical_model=None`: deterministic diurnal sine + noise (stub).
  - `physical_model=PVPhysicalModel(...)`: real irradiance via Open-Meteo + pvlib.

FlexibleLoad and Battery remain analytic — ResStock integration is future work.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eflux.data.pv_model import PVPhysicalModel


@dataclass
class PV:
    kw_peak: float
    noise_std: float = 0.1
    physical_model: "PVPhysicalModel | None" = field(default=None, repr=False)

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
            except Exception:
                # Don't crash the simulator tick on bad data.
                pass
        hour = sim_ts.hour + sim_ts.minute / 60.0
        if 6 <= hour <= 18:
            sun = math.sin(math.pi * (hour - 6) / 12)
        else:
            sun = 0.0
        noisy = sun * (1.0 + rng.gauss(0.0, self.noise_std))
        return max(0.0, self.kw_peak * noisy)


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

    def draw_kw(self, sim_ts: datetime, rng: random.Random) -> float:
        """Stub: daily profile peaking morning + evening. To be replaced by ResStock data."""
        hour = sim_ts.hour + sim_ts.minute / 60.0
        # Two peaks: 7-9 morning, 18-22 evening.
        morning = math.exp(-0.5 * ((hour - 8) / 1.5) ** 2)
        evening = math.exp(-0.5 * ((hour - 20) / 2.0) ** 2)
        profile = 0.4 + 0.4 * (morning + evening)
        noise = rng.gauss(0.0, self.noise_std)
        return max(0.0, self.base_kw * (profile + noise))
