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


@dataclass(slots=True)
class SimpleOrderReservation:
    order_id: int
    interval: DeliveryInterval
    side: str
    resting_terminal_kwh: float
    committed_terminal_kwh: float = 0.0

    @property
    def total_terminal_kwh(self) -> float:
        return self.resting_terminal_kwh + self.committed_terminal_kwh


class BalanceReservationBook:
    """Reserve a forecast ambient renewable/load position without double quoting it."""

    def __init__(self) -> None:
        self._projected_net_kwh: dict[str, float] = {}
        self._orders: dict[int, SimpleOrderReservation] = {}

    def set_projection(self, interval: DeliveryInterval, net_injection_kwh: float) -> None:
        if not math.isfinite(net_injection_kwh):
            raise ValueError("net_injection_kwh must be finite")
        old = self._projected_net_kwh.get(interval.interval_id)
        self._projected_net_kwh[interval.interval_id] = net_injection_kwh
        try:
            self._validate_interval(interval.interval_id)
        except ReservationRejected:
            if old is None:
                del self._projected_net_kwh[interval.interval_id]
            else:
                self._projected_net_kwh[interval.interval_id] = old
            raise

    def reserve(
        self,
        *,
        order_id: int,
        interval: DeliveryInterval,
        side: str,
        terminal_kwh: float,
    ) -> SimpleOrderReservation:
        if order_id in self._orders:
            raise ValueError(f"order {order_id} already has a balance reservation")
        if side not in {"buy", "sell"}:
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        BatteryReservationBook._require_positive(terminal_kwh, "terminal_kwh")
        if interval.interval_id not in self._projected_net_kwh:
            raise ReservationRejected(
                f"no ambient energy projection for interval {interval.interval_id}"
            )
        reservation = SimpleOrderReservation(order_id, interval, side, terminal_kwh)
        self._orders[order_id] = reservation
        try:
            self._validate_interval(interval.interval_id)
        except ReservationRejected:
            del self._orders[order_id]
            raise
        return reservation

    def commit_fill(self, order_id: int, terminal_kwh: float) -> None:
        _commit_simple_fill(self._orders, order_id, terminal_kwh)

    def cancel_unfilled(self, order_id: int) -> float:
        return _cancel_simple_unfilled(self._orders, order_id)

    def settle_interval(self, interval_id: str) -> None:
        self._orders = {
            oid: order
            for oid, order in self._orders.items()
            if order.interval.interval_id != interval_id
        }
        self._projected_net_kwh.pop(interval_id, None)

    def _validate_interval(self, interval_id: str) -> None:
        projected = self._projected_net_kwh[interval_id]
        orders = [
            order for order in self._orders.values() if order.interval.interval_id == interval_id
        ]
        sells = sum(order.total_terminal_kwh for order in orders if order.side == "sell")
        buys = sum(order.total_terminal_kwh for order in orders if order.side == "buy")
        available_sell = max(0.0, projected)
        available_buy = max(0.0, -projected)
        if sells > available_sell + 1e-9:
            raise ReservationRejected(
                f"balance sells {sells:.6f} kWh exceed projected surplus {available_sell:.6f} kWh"
            )
        if buys > available_buy + 1e-9:
            raise ReservationRejected(
                f"balance buys {buys:.6f} kWh exceed projected deficit {available_buy:.6f} kWh"
            )


@dataclass(frozen=True, slots=True)
class DispatchableIntervalProjection:
    interval_id: str
    contracted_terminal_kwh: float
    scheduled_terminal_kwh: float
    average_power_kw: float


class DispatchableReservationBook:
    """Worst-case capacity/ramp reservation for fuel-backed sell orders."""

    def __init__(
        self,
        *,
        max_power_kw: float,
        min_power_kw: float = 0.0,
        ramp_kw_per_min: float | None = None,
        initial_power_kw: float = 0.0,
    ) -> None:
        values = (max_power_kw, min_power_kw, initial_power_kw)
        if not all(math.isfinite(value) and value >= 0.0 for value in values):
            raise ValueError("dispatchable power values must be finite and non-negative")
        if min_power_kw > max_power_kw:
            raise ValueError("min_power_kw cannot exceed max_power_kw")
        if initial_power_kw > max_power_kw:
            raise ValueError("initial_power_kw cannot exceed max_power_kw")
        if ramp_kw_per_min is not None and (
            not math.isfinite(ramp_kw_per_min) or ramp_kw_per_min <= 0.0
        ):
            raise ValueError("ramp_kw_per_min must be finite and positive when set")
        self.max_power_kw = max_power_kw
        self.min_power_kw = min_power_kw
        self.ramp_kw_per_min = ramp_kw_per_min
        self.initial_power_kw = initial_power_kw
        self._orders: dict[int, SimpleOrderReservation] = {}

    def reserve(
        self, *, order_id: int, interval: DeliveryInterval, terminal_kwh: float
    ) -> SimpleOrderReservation:
        if order_id in self._orders:
            raise ValueError(f"order {order_id} already has a dispatchable reservation")
        BatteryReservationBook._require_positive(terminal_kwh, "terminal_kwh")
        reservation = SimpleOrderReservation(order_id, interval, "sell", terminal_kwh)
        self._orders[order_id] = reservation
        try:
            self.project()
        except ReservationRejected:
            del self._orders[order_id]
            raise
        return reservation

    def commit_fill(self, order_id: int, terminal_kwh: float) -> None:
        _commit_simple_fill(self._orders, order_id, terminal_kwh)

    def cancel_unfilled(self, order_id: int) -> float:
        return _cancel_simple_unfilled(self._orders, order_id)

    def settle_interval(self, interval_id: str, *, ending_power_kw: float) -> None:
        if not math.isfinite(ending_power_kw) or not 0.0 <= ending_power_kw <= self.max_power_kw:
            raise ValueError("ending_power_kw must be within dispatchable capacity")
        self._orders = {
            oid: order
            for oid, order in self._orders.items()
            if order.interval.interval_id != interval_id
        }
        self.initial_power_kw = ending_power_kw
        self.project()

    def project(self) -> tuple[DispatchableIntervalProjection, ...]:
        grouped: dict[str, list[SimpleOrderReservation]] = {}
        intervals: dict[str, DeliveryInterval] = {}
        for reservation in self._orders.values():
            iid = reservation.interval.interval_id
            grouped.setdefault(iid, []).append(reservation)
            intervals[iid] = reservation.interval
        prior_power = self.initial_power_kw
        prior_interval: DeliveryInterval | None = None
        out: list[DispatchableIntervalProjection] = []
        for iid in sorted(grouped, key=lambda key: intervals[key].start):
            interval = intervals[iid]
            contracted = sum(order.total_terminal_kwh for order in grouped[iid])
            min_energy = energy_kwh_from_average_power(self.min_power_kw, interval.duration_sec)
            scheduled = max(contracted, min_energy) if contracted > 0.0 else 0.0
            max_energy = energy_kwh_from_average_power(self.max_power_kw, interval.duration_sec)
            if scheduled > max_energy + 1e-9:
                raise ReservationRejected(
                    f"dispatchable energy {scheduled:.6f} kWh exceeds interval capacity "
                    f"{max_energy:.6f} kWh"
                )
            power = scheduled * 3600.0 / interval.duration_sec
            if self.ramp_kw_per_min is not None:
                if prior_interval is None:
                    ramp_minutes = interval.duration_sec / 60.0
                else:
                    ramp_minutes = max(
                        interval.duration_sec / 60.0,
                        (interval.start - prior_interval.start).total_seconds() / 60.0,
                    )
                allowed = self.ramp_kw_per_min * ramp_minutes
                if abs(power - prior_power) > allowed + 1e-9:
                    raise ReservationRejected(
                        f"dispatchable ramp {abs(power - prior_power):.6f} kW exceeds "
                        f"{allowed:.6f} kW"
                    )
            out.append(DispatchableIntervalProjection(iid, contracted, scheduled, power))
            prior_power = power
            prior_interval = interval
        return tuple(out)


def _commit_simple_fill(
    orders: dict[int, SimpleOrderReservation], order_id: int, terminal_kwh: float
) -> None:
    BatteryReservationBook._require_positive(terminal_kwh, "terminal_kwh")
    reservation = orders.get(order_id)
    if reservation is None:
        raise KeyError(order_id)
    if terminal_kwh > reservation.resting_terminal_kwh + 1e-9:
        raise ValueError("fill exceeds resting resource reservation")
    reservation.resting_terminal_kwh = max(0.0, reservation.resting_terminal_kwh - terminal_kwh)
    reservation.committed_terminal_kwh += terminal_kwh


def _cancel_simple_unfilled(orders: dict[int, SimpleOrderReservation], order_id: int) -> float:
    reservation = orders.get(order_id)
    if reservation is None:
        return 0.0
    released = reservation.resting_terminal_kwh
    reservation.resting_terminal_kwh = 0.0
    if reservation.committed_terminal_kwh <= 1e-12:
        del orders[order_id]
    return released
