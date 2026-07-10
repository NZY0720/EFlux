from __future__ import annotations

import pytest

from eflux.vpp.base import VPPParams


def test_vpp_params_expose_explicit_v2_economic_and_physical_defaults():
    params = VPPParams()
    assert params.battery_initial_soc_frac == 0.5
    assert params.battery_degradation_cost_per_mwh_throughput == 20.0
    assert params.value_of_lost_load_per_mwh == 10000.0
    assert params.gas_cost_per_mwh == 60.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"battery_kwh": -1.0},
        {"battery_eta_rt": 0.0},
        {"battery_eta_rt": 1.1},
        {"battery_initial_soc_frac": -0.1},
        {"battery_initial_soc_frac": 1.1},
        {"gas_kw_max": 2.0, "gas_min_kw": 3.0},
        {"gas_ramp_kw_per_min": 0.0},
        {"value_of_lost_load_per_mwh": -1.0},
    ],
)
def test_vpp_params_reject_physically_invalid_values(kwargs):
    with pytest.raises(ValueError):
        VPPParams(**kwargs)
