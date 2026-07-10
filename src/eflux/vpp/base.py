"""VPP parameter & live-state containers.

A VPP is an economic actor backed by a portfolio of DERs (PV + battery + flexible load).
- params: static configuration declared at registration (DER capacities, preferences).
- state: live snapshot recomputed each tick (SOC, current PV output, load draw, forecast).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class VPPParams:
    pv_kw_peak: float = 5.0
    battery_kwh: float = 10.0
    battery_kw_max: float = 3.0
    battery_eta_rt: float = 0.9  # round-trip efficiency
    battery_initial_soc_frac: float = 0.5
    # Economic wear charged per MWh of absolute cell-energy throughput.
    battery_degradation_cost_per_mwh_throughput: float = 20.0
    load_kw_base: float = 1.5
    load_elasticity: float = 0.2  # fractional flex around base
    # Daily load shape: residential | industrial | commercial | flat.
    load_profile: str = "residential"
    # Wind turbine (0 = no wind). Stub base speed unless site coords attach
    # real Open-Meteo wind data (see pv_lat/pv_lon — they are site coords).
    wind_kw_rated: float = 0.0
    wind_mean_speed: float = 7.0  # m/s, stub base
    # Dispatchable gas generation (0 = none). The GasGeneratorAgent offers this
    # capacity at its marginal cost; energy comes from fuel, not the balance.
    gas_kw_max: float = 0.0
    gas_min_kw: float = 0.0
    gas_ramp_kw_per_min: float | None = None
    gas_cost_per_mwh: float = 60.0
    gas_startup_cost_usd: float = 0.0
    value_of_lost_load_per_mwh: float = 10000.0
    starting_cash_usd: float = 0.0
    risk_aversion: float = 0.5  # 0 = risk neutral, 1 = very averse
    forecast_noise_std: float = 0.1
    markup_floor: float = 0.0  # min markup over marginal cost (sell side)
    markup_ceiling: float = 1.0  # max markdown below marginal value (buy side)
    # Optional site coordinates: enable the Open-Meteo weather fetch that feeds
    # both the pvlib PV model and real wind speeds. None on either disables it.
    pv_lat: float | None = None
    pv_lon: float | None = None
    pv_tilt: float = 30.0  # degrees from horizontal
    pv_azimuth: float = 180.0  # degrees clockwise from north (180 = south, equator-facing)

    def __post_init__(self) -> None:
        nonnegative = (
            "pv_kw_peak",
            "battery_kwh",
            "battery_kw_max",
            "battery_degradation_cost_per_mwh_throughput",
            "load_kw_base",
            "load_elasticity",
            "wind_kw_rated",
            "wind_mean_speed",
            "gas_kw_max",
            "gas_min_kw",
            "gas_cost_per_mwh",
            "gas_startup_cost_usd",
            "value_of_lost_load_per_mwh",
            "forecast_noise_std",
            "markup_floor",
            "markup_ceiling",
        )
        for name in nonnegative:
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not 0.0 < self.battery_eta_rt <= 1.0:
            raise ValueError("battery_eta_rt must be in (0, 1]")
        if not 0.0 <= self.battery_initial_soc_frac <= 1.0:
            raise ValueError("battery_initial_soc_frac must be in [0, 1]")
        if not 0.0 <= self.risk_aversion <= 1.0:
            raise ValueError("risk_aversion must be in [0, 1]")
        if self.gas_min_kw > self.gas_kw_max:
            raise ValueError("gas_min_kw cannot exceed gas_kw_max")
        if self.gas_ramp_kw_per_min is not None and (
            not math.isfinite(self.gas_ramp_kw_per_min) or self.gas_ramp_kw_per_min <= 0.0
        ):
            raise ValueError("gas_ramp_kw_per_min must be finite and positive when set")
        if not math.isfinite(self.starting_cash_usd):
            raise ValueError("starting_cash_usd must be finite")

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
    wind_kw: float = 0.0  # instantaneous wind output (kW)
    load_kw: float = 0.0  # instantaneous load (kW)
    net_kw: float = 0.0  # positive = surplus (sell pressure), negative = deficit (buy pressure)
    # Untraded energy balance accumulated across ticks (kWh). Positive = surplus
    # waiting to be sold, negative = deficit waiting to be bought. With a 1-second
    # tick the per-tick net energy (~1e-3 kWh) is far below any sane order size, so
    # agents quote from this accumulator once it clears their min_qty threshold.
    # The runner updates this only with energy the battery cannot buffer or fills
    # that clear the forced position.
    pending_net_kwh: float = 0.0
    pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    cumulative_energy_sold_kwh: float = 0.0
    cumulative_energy_bought_kwh: float = 0.0

    def update_net(self) -> None:
        self.net_kw = self.pv_kw + self.wind_kw - self.load_kw
