"""VPP parameter & live-state containers.

A VPP is an economic actor backed by a portfolio of DERs (PV + battery + flexible load).
- params: static configuration declared at registration (DER capacities, preferences).
- state: live snapshot recomputed each tick (SOC, current PV output, load draw, forecast).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class VPPParams:
    pv_kw_peak: float = 5.0
    battery_kwh: float = 10.0
    battery_kw_max: float = 3.0
    battery_eta_rt: float = 0.9  # round-trip efficiency
    load_kw_base: float = 1.5
    load_elasticity: float = 0.2  # fractional flex around base
    risk_aversion: float = 0.5  # 0 = risk neutral, 1 = very averse
    forecast_noise_std: float = 0.1
    markup_floor: float = 0.0  # min markup over marginal cost (sell side)
    markup_ceiling: float = 1.0  # max markdown below marginal value (buy side)
    # Optional PV physical-model geometry (set to use Open-Meteo + pvlib instead
    # of the stub diurnal sine). None on either lat/lon disables the physical model.
    pv_lat: float | None = None
    pv_lon: float | None = None
    pv_tilt: float = 30.0       # degrees from horizontal
    pv_azimuth: float = 180.0   # degrees clockwise from north (180 = south, equator-facing)

    @classmethod
    def from_dict(cls, d: dict) -> VPPParams:
        defaults = cls()
        merged = {f: d.get(f, getattr(defaults, f)) for f in cls.__dataclass_fields__}
        return cls(**merged)

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


@dataclass
class VPPState:
    sim_ts: datetime
    soc_kwh: float = 5.0  # current SOC
    pv_kw: float = 0.0  # instantaneous PV output (kW)
    load_kw: float = 0.0  # instantaneous load (kW)
    net_kw: float = 0.0  # positive = surplus (sell pressure), negative = deficit (buy pressure)
    pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    cumulative_energy_sold_kwh: float = 0.0
    cumulative_energy_bought_kwh: float = 0.0

    def update_net(self) -> None:
        self.net_kw = self.pv_kw - self.load_kw
