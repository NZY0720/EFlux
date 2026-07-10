from __future__ import annotations

import pytest

from eflux.vpp.der import Battery


def test_round_trip_efficiency_is_applied_physically_once_per_leg():
    battery = Battery(capacity_kwh=2.0, max_power_kw=1.0, eta_rt=0.9, soc_kwh=0.0)
    charged = battery.execute_terminal_interval(charge_terminal_kwh=1.0, duration_h=1.0)
    assert charged.ending_soc_kwh == pytest.approx(0.9**0.5)
    discharged = battery.execute_terminal_interval(discharge_terminal_kwh=0.9, duration_h=1.0)
    assert discharged.ending_soc_kwh == pytest.approx(0.0)
    assert discharged.discharge_terminal_kwh == pytest.approx(0.9)


def test_delivery_rejects_infeasible_energy_instead_of_clamping_soc():
    battery = Battery(capacity_kwh=1.0, max_power_kw=4.0, eta_rt=0.9, soc_kwh=0.2)
    with pytest.raises(ValueError, match="only"):
        battery.execute_terminal_interval(discharge_terminal_kwh=0.2, duration_h=5 / 60)
    assert battery.soc_kwh == pytest.approx(0.2)


def test_two_sided_delivery_uses_gross_inverter_power():
    battery = Battery(capacity_kwh=1.0, max_power_kw=4.0, eta_rt=0.9, soc_kwh=0.5)
    with pytest.raises(ValueError, match="power budget"):
        battery.execute_terminal_interval(
            charge_terminal_kwh=0.2,
            discharge_terminal_kwh=0.2,
            duration_h=5 / 60,
        )


def test_valid_two_sided_delivery_accounts_for_both_efficiency_losses():
    battery = Battery(capacity_kwh=1.0, max_power_kw=4.0, eta_rt=0.9, soc_kwh=0.5)
    result = battery.execute_terminal_interval(
        charge_terminal_kwh=0.1,
        discharge_terminal_kwh=0.1,
        duration_h=5 / 60,
    )
    assert result.cell_throughput_kwh == pytest.approx(0.1 * 0.9**0.5 + 0.1 / 0.9**0.5)
    assert result.ending_soc_kwh < result.starting_soc_kwh
