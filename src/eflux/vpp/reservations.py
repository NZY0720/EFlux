"""Worst-case resource reservation for resting and filled battery orders.

All quantities at the public boundary are terminal kWh.  Reservations translate
them into cell-energy and shared-inverter constraints over complete delivery
intervals.  Every resting order is assumed to fill; that conservative rule is
what prevents several individually-valid quotes from collectively overselling
SOC or interval power.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from eflux.market.products import DeliveryInterval, energy_kwh_from_average_power


class ReservationRejected(ValueError):
    pass


@dataclass(slots=True)
class BatteryOrderReservation:
    order_id: int
    interval: DeliveryInterval
    side: str
    resting_terminal_kwh: float
    committed_terminal_kwh: float = 0.0

    @property
    def total_terminal_kwh(self) -> float:
        return self.resting_terminal_kwh + self.committed_terminal_kwh


@dataclass(frozen=True, slots=True)
class BatteryIntervalProjection:
    interval_id: str
    starting_soc_kwh: float
    ending_soc_kwh: float
    charge_terminal_kwh: float
    discharge_terminal_kwh: float


class BatteryReservationBook:
    def __init__(
        self,
        *,
        capacity_kwh: float,
        max_power_kw: float,
        eta_rt: float,
        initial_soc_kwh: float,
    ) -> None:
        if not math.isfinite(capacity_kwh) or capacity_kwh < 0.0:
            raise ValueError("capacity_kwh must be finite and non-negative")
        if not math.isfinite(max_power_kw) or max_power_kw < 0.0:
            raise ValueError("max_power_kw must be finite and non-negative")
        if not math.isfinite(eta_rt) or not 0.0 < eta_rt <= 1.0:
            raise ValueError("eta_rt must be in (0, 1]")
        if not math.isfinite(initial_soc_kwh) or not 0.0 <= initial_soc_kwh <= capacity_kwh:
            raise ValueError("initial_soc_kwh must be within battery capacity")
        self.capacity_kwh = capacity_kwh
        self.max_power_kw = max_power_kw
        self.eta_rt = eta_rt
        self.eta_charge = math.sqrt(eta_rt)
        self.eta_discharge = math.sqrt(eta_rt)
        self.initial_soc_kwh = initial_soc_kwh
        self._orders: dict[int, BatteryOrderReservation] = {}

    @property
    def orders(self) -> tuple[BatteryOrderReservation, ...]:
        return tuple(self._orders.values())

    def set_initial_soc(self, soc_kwh: float) -> None:
        if not math.isfinite(soc_kwh) or not 0.0 <= soc_kwh <= self.capacity_kwh:
            raise ValueError("soc_kwh must be within battery capacity")
        old = self.initial_soc_kwh
        self.initial_soc_kwh = soc_kwh
        try:
            self.project()
        except ReservationRejected:
            self.initial_soc_kwh = old
            raise

    def reserve(
        self,
        *,
        order_id: int,
        interval: DeliveryInterval,
        side: str,
        terminal_kwh: float,
    ) -> BatteryOrderReservation:
        if order_id in self._orders:
            raise ValueError(f"order {order_id} already has a battery reservation")
        self._validate_order(side, terminal_kwh)
        reservation = BatteryOrderReservation(order_id, interval, side, terminal_kwh)
        self._orders[order_id] = reservation
        try:
            self.project()
        except ReservationRejected:
            del self._orders[order_id]
            raise
        return reservation

    def commit_fill(self, order_id: int, terminal_kwh: float) -> None:
        self._require_positive(terminal_kwh, "terminal_kwh")
        reservation = self._orders.get(order_id)
        if reservation is None:
            raise KeyError(order_id)
        if terminal_kwh > reservation.resting_terminal_kwh + 1e-9:
            raise ValueError("fill exceeds resting battery reservation")
        reservation.resting_terminal_kwh = max(0.0, reservation.resting_terminal_kwh - terminal_kwh)
        reservation.committed_terminal_kwh += terminal_kwh

    def cancel_unfilled(self, order_id: int) -> float:
        reservation = self._orders.get(order_id)
        if reservation is None:
            return 0.0
        released = reservation.resting_terminal_kwh
        reservation.resting_terminal_kwh = 0.0
        if reservation.committed_terminal_kwh <= 1e-12:
            del self._orders[order_id]
        return released

    def settle_interval(self, interval_id: str, *, ending_soc_kwh: float) -> None:
        if not math.isfinite(ending_soc_kwh) or not 0.0 <= ending_soc_kwh <= self.capacity_kwh:
            raise ValueError("ending_soc_kwh must be within battery capacity")
        self._orders = {
            oid: reservation
            for oid, reservation in self._orders.items()
            if reservation.interval.interval_id != interval_id
        }
        self.initial_soc_kwh = ending_soc_kwh
        self.project()

    def project(self) -> tuple[BatteryIntervalProjection, ...]:
        grouped: dict[str, list[BatteryOrderReservation]] = {}
        intervals: dict[str, DeliveryInterval] = {}
        for reservation in self._orders.values():
            iid = reservation.interval.interval_id
            grouped.setdefault(iid, []).append(reservation)
            intervals[iid] = reservation.interval

        soc = self.initial_soc_kwh
        projections: list[BatteryIntervalProjection] = []
        for iid in sorted(grouped, key=lambda key: intervals[key].start):
            interval = intervals[iid]
            charge = sum(r.total_terminal_kwh for r in grouped[iid] if r.side == "buy")
            discharge = sum(r.total_terminal_kwh for r in grouped[iid] if r.side == "sell")
            terminal_power_budget = energy_kwh_from_average_power(
                self.max_power_kw, interval.duration_sec
            )
            # One bidirectional inverter cannot charge and discharge at full power
            # simultaneously. Reserve the gross terminal throughput, not the net.
            if charge + discharge > terminal_power_budget + 1e-9:
                raise ReservationRejected(
                    f"interval {iid} gross battery energy {charge + discharge:.6f} kWh "
                    f"exceeds {terminal_power_budget:.6f} kWh power budget"
                )
            cell_charge = charge * self.eta_charge
            cell_discharge = discharge / self.eta_discharge
            # Worst-case intra-interval ordering: discharge can happen before charge,
            # or charge before discharge. Both sequences must be feasible.
            if cell_discharge > soc + 1e-9:
                raise ReservationRejected(
                    f"interval {iid} discharge requires {cell_discharge:.6f} cell kWh; "
                    f"only {soc:.6f} available"
                )
            if cell_charge > self.capacity_kwh - soc + 1e-9:
                raise ReservationRejected(
                    f"interval {iid} charge requires {cell_charge:.6f} cell kWh room; "
                    f"only {self.capacity_kwh - soc:.6f} available"
                )
            end = soc + cell_charge - cell_discharge
            if not -1e-9 <= end <= self.capacity_kwh + 1e-9:
                raise ReservationRejected(f"interval {iid} ends at infeasible SOC {end:.6f}")
            projections.append(
                BatteryIntervalProjection(
                    interval_id=iid,
                    starting_soc_kwh=soc,
                    ending_soc_kwh=min(self.capacity_kwh, max(0.0, end)),
                    charge_terminal_kwh=charge,
                    discharge_terminal_kwh=discharge,
                )
            )
            soc = end
        return tuple(projections)

    @staticmethod
    def _validate_order(side: str, terminal_kwh: float) -> None:
        if side not in {"buy", "sell"}:
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        BatteryReservationBook._require_positive(terminal_kwh, "terminal_kwh")

    @staticmethod
    def _require_positive(value: float, field: str) -> None:
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{field} must be finite and positive")
