from __future__ import annotations

import pytest

from eflux.evaluation.arb_iq import oracle_arb_profit, realized_arb_profit, spread_capture


def test_oracle_charges_at_cheapest_and_discharges_at_dearest() -> None:
    # eta_c = eta_d = 0.9. One kWh bought at $10 stores 0.9 kWh,
    # which later exports 0.81 kWh at $100: (81 - 10) / 1000 = $0.071.
    profit = oracle_arb_profit(
        [100.0, 10.0, 100.0, 100.0],
        battery_kwh=1.0,
        battery_kw_max=1.0,
        interval_h=1.0,
        round_trip_eff=0.81,
        start_soc=0.0,
    )
    assert profit == pytest.approx(0.071)


def test_realized_profit_fifo_and_unmatched_inventory() -> None:
    trades = [
        {"side": "sell", "price": 500, "qty": 3},  # no prior buy: ignored
        {"side": "buy", "price": 20, "qty": 2},
        {"side": "buy", "price": 40, "qty": 3},
        {"side": "sell", "price": 100, "qty": 4},
        {"side": "buy", "price": 1, "qty": 9},  # unmatched: ignored
    ]
    # 2 kWh from the $20 lot and 2 kWh from the $40 lot are sold at $100.
    assert realized_arb_profit(trades) == pytest.approx((2 * 80 + 2 * 60) / 1000)


def test_realized_profit_supports_quantity_weighting_and_losses() -> None:
    trades = [
        {"side": "buy", "price": 100, "quantity": 1.5},
        {"side": "sell", "price": 80, "quantity": 1.0},
    ]
    assert realized_arb_profit(trades) == pytest.approx(-0.02)


@pytest.mark.parametrize(
    ("realized", "oracle", "expected"),
    [
        (1.0, 2.0, 0.5),
        (2.0, 1.0, 1.5),
        (-1.0, 2.0, 0.0),
        (1.0, 0.0, None),
        (1.0, -1.0, None),
        (float("nan"), 1.0, None),
    ],
)
def test_spread_capture_edges(realized: float, oracle: float, expected: float | None) -> None:
    assert spread_capture(realized, oracle) == expected
