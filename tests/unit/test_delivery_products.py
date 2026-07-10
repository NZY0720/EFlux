from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eflux.market.delivery import DeliveryPosition
from eflux.market.products import (
    DeliveryInterval,
    average_power_kw_from_energy,
    delivery_horizon,
    delivery_interval_containing,
    energy_kwh_from_average_power,
    next_delivery_interval,
)


def _interval() -> DeliveryInterval:
    start = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
    return DeliveryInterval(
        market="p2p",
        start=start,
        end=start + timedelta(minutes=5),
        gate_closure=start,
        opens_at=start - timedelta(minutes=30),
    )


def test_power_and_energy_are_inverse_over_delivery_duration():
    # A 4 kW battery can deliver at most 1/3 kWh over a full five-minute product.
    energy = energy_kwh_from_average_power(4.0, 5 * 60)
    assert energy == pytest.approx(1.0 / 3.0)
    assert average_power_kw_from_energy(energy, 5 * 60) == pytest.approx(4.0)


def test_next_product_targets_a_complete_future_interval():
    product = next_delivery_interval(datetime(2026, 7, 11, 12, 3, 20, tzinfo=UTC))
    assert product.start == datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
    assert product.end == datetime(2026, 7, 11, 12, 10, tzinfo=UTC)
    assert product.gate_closure == product.start
    assert product.is_trading_open(datetime(2026, 7, 11, 12, 3, 20, tzinfo=UTC))
    assert not product.is_trading_open(product.start)


def test_delivery_horizon_contains_consecutive_complete_products():
    products = delivery_horizon(datetime(2026, 7, 11, 12, 3, 20, tzinfo=UTC), count=3)
    assert [product.start.minute for product in products] == [5, 10, 15]
    assert all(product.duration_sec == 300 for product in products)
    assert products[0].end == products[1].start


def test_delivery_interval_containing_uses_current_aligned_window():
    product = delivery_interval_containing(datetime(2026, 7, 11, 12, 7, 30, tzinfo=UTC))
    assert product.start == datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
    assert product.end == datetime(2026, 7, 11, 12, 10, tzinfo=UTC)


def test_interval_rejects_gate_closure_after_delivery_starts():
    start = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
    with pytest.raises(ValueError, match="gate_closure"):
        DeliveryInterval(
            market="p2p",
            start=start,
            end=start + timedelta(minutes=5),
            gate_closure=start + timedelta(seconds=1),
            opens_at=start - timedelta(minutes=30),
        )


@pytest.mark.parametrize(
    ("position", "expected_physical", "expected_contract", "expected_imbalance"),
    [
        # One kWh renewable export matched by a one kWh sell contract.
        (DeliveryPosition(_interval(), renewable_generation_kwh=1, contracted_sell_kwh=1), 1, 1, 0),
        # One kWh load matched by a one kWh buy contract.
        (DeliveryPosition(_interval(), load_demand_kwh=1, contracted_buy_kwh=1), -1, -1, 0),
        # Grid/P2P energy bought specifically to charge the battery.
        (
            DeliveryPosition(_interval(), battery_charge_terminal_kwh=1, contracted_buy_kwh=1),
            -1,
            -1,
            0,
        ),
        # Battery discharge sold into the interval.
        (
            DeliveryPosition(_interval(), battery_discharge_terminal_kwh=1, contracted_sell_kwh=1),
            1,
            1,
            0,
        ),
        # A flexible-load buy becomes additional metered consumption.
        (
            DeliveryPosition(_interval(), flexible_load_demand_kwh=1, contracted_buy_kwh=1),
            -1,
            -1,
            0,
        ),
        # A 1 kWh sale backed by only 0.75 kWh physical delivery is 0.25 kWh short.
        (
            DeliveryPosition(_interval(), renewable_generation_kwh=0.75, contracted_sell_kwh=1),
            0.75,
            1,
            -0.25,
        ),
    ],
)
def test_delivery_position_sign_convention(
    position, expected_physical, expected_contract, expected_imbalance
):
    position.validate()
    assert position.physical_net_injection_kwh == pytest.approx(expected_physical)
    assert position.contracted_net_injection_kwh == pytest.approx(expected_contract)
    assert position.imbalance_kwh == pytest.approx(expected_imbalance)


def test_position_tracks_curtailment_and_unserved_load_separately_from_imbalance():
    position = DeliveryPosition(
        _interval(),
        renewable_generation_kwh=2.0,
        curtailed_generation_kwh=0.5,
        load_demand_kwh=2.0,
        unserved_load_kwh=0.25,
        contracted_buy_kwh=0.25,
    )
    position.validate()
    assert position.delivered_renewable_kwh == pytest.approx(1.5)
    assert position.served_load_kwh == pytest.approx(1.75)
    assert position.physical_net_injection_kwh == pytest.approx(-0.25)
    assert position.imbalance_kwh == pytest.approx(0.0)
