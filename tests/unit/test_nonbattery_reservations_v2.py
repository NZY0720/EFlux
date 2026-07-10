from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eflux.market.products import DeliveryInterval
from eflux.vpp.reservations import (
    BalanceReservationBook,
    DispatchableReservationBook,
    ReservationRejected,
)


def _interval(minute: int = 5) -> DeliveryInterval:
    start = datetime(2026, 7, 11, 12, minute, tzinfo=UTC)
    return DeliveryInterval(
        "p2p", start, start + timedelta(minutes=5), start, start - timedelta(minutes=30)
    )


def test_balance_orders_cannot_collectively_double_sell_projected_surplus():
    interval = _interval()
    book = BalanceReservationBook()
    book.set_projection(interval, 1.0)
    book.reserve(order_id=1, interval=interval, side="sell", terminal_kwh=0.6)
    with pytest.raises(ReservationRejected, match="projected surplus"):
        book.reserve(order_id=2, interval=interval, side="sell", terminal_kwh=0.5)


def test_balance_side_must_match_projected_position():
    interval = _interval()
    book = BalanceReservationBook()
    book.set_projection(interval, -1.0)
    with pytest.raises(ReservationRejected, match="projected surplus"):
        book.reserve(order_id=1, interval=interval, side="sell", terminal_kwh=0.1)
    book.reserve(order_id=2, interval=interval, side="buy", terminal_kwh=1.0)


def test_dispatchable_orders_share_interval_capacity():
    interval = _interval()
    book = DispatchableReservationBook(max_power_kw=6.0)
    book.reserve(order_id=1, interval=interval, terminal_kwh=0.3)
    with pytest.raises(ReservationRejected, match="capacity"):
        book.reserve(order_id=2, interval=interval, terminal_kwh=0.21)


def test_dispatchable_minimum_output_is_scheduled_and_ramp_checked():
    interval = _interval()
    book = DispatchableReservationBook(
        max_power_kw=10.0,
        min_power_kw=4.0,
        ramp_kw_per_min=0.5,
        initial_power_kw=0.0,
    )
    # Starting 4 kW within five minutes requires a 4 kW ramp, above the 2.5 kW limit.
    with pytest.raises(ReservationRejected, match="ramp"):
        book.reserve(order_id=1, interval=interval, terminal_kwh=0.1)


def test_dispatchable_commitment_survives_cancel_of_unfilled_remainder():
    interval = _interval()
    book = DispatchableReservationBook(max_power_kw=10.0)
    reservation = book.reserve(order_id=1, interval=interval, terminal_kwh=0.5)
    book.commit_fill(1, 0.2)
    assert book.cancel_unfilled(1) == pytest.approx(0.3)
    projection = book.project()[0]
    assert reservation.committed_terminal_kwh == pytest.approx(0.2)
    assert projection.committed_terminal_kwh == pytest.approx(0.2)
    assert projection.resting_terminal_kwh == pytest.approx(0.0)


def test_later_dispatchable_quote_cannot_depend_on_optional_prior_ramp():
    first = _interval(5)
    second = _interval(10)
    book = DispatchableReservationBook(
        max_power_kw=10.0,
        ramp_kw_per_min=1.0,
        initial_power_kw=0.0,
    )
    # The first resting quote creates a reachable 0..5 kW envelope.  An 8 kW
    # second-interval quote would be feasible only if the first quote filled.
    book.reserve(order_id=1, interval=first, terminal_kwh=5.0 * 5.0 / 60.0)
    with pytest.raises(ReservationRejected, match="ramp envelope"):
        book.reserve(order_id=2, interval=second, terminal_kwh=8.0 * 5.0 / 60.0)


def test_committed_prior_dispatch_may_back_later_ramp():
    first = _interval(5)
    second = _interval(10)
    book = DispatchableReservationBook(
        max_power_kw=10.0,
        ramp_kw_per_min=1.0,
        initial_power_kw=0.0,
    )
    first_energy = 5.0 * 5.0 / 60.0
    book.reserve(order_id=1, interval=first, terminal_kwh=first_energy)
    book.commit_fill(1, first_energy)
    book.reserve(order_id=2, interval=second, terminal_kwh=8.0 * 5.0 / 60.0)
    projections = book.project()
    assert projections[0].average_power_min_kw == pytest.approx(5.0)
    assert projections[0].average_power_max_kw == pytest.approx(5.0)
