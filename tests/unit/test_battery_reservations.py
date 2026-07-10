from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eflux.market.products import DeliveryInterval
from eflux.vpp.reservations import BatteryReservationBook, ReservationRejected


def _interval(minute: int = 5) -> DeliveryInterval:
    start = datetime(2026, 7, 11, 12, minute, tzinfo=UTC)
    return DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )


def _book(*, soc: float = 0.5, capacity: float = 1.0, power: float = 4.0):
    return BatteryReservationBook(
        capacity_kwh=capacity,
        max_power_kw=power,
        eta_rt=0.9,
        initial_soc_kwh=soc,
    )


def test_interval_power_budget_is_collective_across_resting_orders():
    book = _book()
    interval = _interval()
    book.reserve(order_id=1, interval=interval, side="buy", terminal_kwh=0.2)
    with pytest.raises(ReservationRejected, match="power budget"):
        book.reserve(order_id=2, interval=interval, side="buy", terminal_kwh=0.14)


def test_bidirectional_quotes_reserve_gross_not_net_inverter_power():
    book = _book()
    interval = _interval()
    book.reserve(order_id=1, interval=interval, side="buy", terminal_kwh=0.2)
    with pytest.raises(ReservationRejected, match="power budget"):
        book.reserve(order_id=2, interval=interval, side="sell", terminal_kwh=0.2)


def test_discharge_reservation_applies_terminal_to_cell_efficiency():
    book = _book(soc=0.2)
    interval = _interval()
    book.reserve(order_id=1, interval=interval, side="sell", terminal_kwh=0.18)
    with pytest.raises(ReservationRejected, match="only"):
        book.reserve(order_id=2, interval=interval, side="sell", terminal_kwh=0.02)


def test_fill_becomes_commitment_and_cancel_releases_only_unfilled_part():
    book = _book()
    interval = _interval()
    reservation = book.reserve(order_id=1, interval=interval, side="sell", terminal_kwh=0.3)
    book.commit_fill(1, 0.1)
    assert reservation.resting_terminal_kwh == pytest.approx(0.2)
    assert reservation.committed_terminal_kwh == pytest.approx(0.1)
    assert book.cancel_unfilled(1) == pytest.approx(0.2)
    assert reservation.total_terminal_kwh == pytest.approx(0.1)
    assert len(book.orders) == 1


def test_future_charge_can_back_a_later_discharge_but_not_beyond_projected_soc():
    book = _book(soc=0.5, power=12.0)
    book.reserve(order_id=1, interval=_interval(5), side="buy", terminal_kwh=0.5)
    book.reserve(order_id=2, interval=_interval(10), side="sell", terminal_kwh=0.9)
    with pytest.raises(ReservationRejected, match="only"):
        book.reserve(order_id=3, interval=_interval(10), side="sell", terminal_kwh=0.05)


def test_settling_interval_removes_commitments_and_reanchors_soc():
    book = _book()
    interval = _interval()
    book.reserve(order_id=1, interval=interval, side="sell", terminal_kwh=0.2)
    book.commit_fill(1, 0.2)
    book.settle_interval(interval.interval_id, ending_soc_kwh=0.3)
    assert not book.orders
    assert book.initial_soc_kwh == pytest.approx(0.3)
