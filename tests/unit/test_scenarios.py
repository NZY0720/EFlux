"""Unit tests for the YAML-driven default scenario."""

from __future__ import annotations

from eflux.agents.gas import GasGeneratorAgent
from eflux.agents.reflective import ReflectiveAgent
from eflux.bridge.bus import InMemoryBus
from eflux.config import get_settings
from eflux.simulator.runner import Simulator
from eflux.simulator.scenarios import load_default_scenario


def _load(monkeypatch) -> Simulator:
    monkeypatch.setenv("EFLUX_PV_PHYSICAL", "false")
    monkeypatch.setenv("EFLUX_REFLECTIVE_ENABLED", "false")
    get_settings.cache_clear()
    sim = Simulator(bus=InMemoryBus())
    load_default_scenario(sim)
    return sim


def test_default_scenario_loads_thirty_vpps_incl_llm(monkeypatch):
    sim = _load(monkeypatch)

    assert len(sim.vpps) == 30
    my_vpps = sim.my_managed_vpps()
    assert len(my_vpps) == 1
    assert my_vpps[0].name == "my-llm-vpp"
    assert isinstance(my_vpps[0].agent, ReflectiveAgent)
    assert len([v for v in sim.vpps.values() if not v.is_my_vpp]) == 29


def test_default_scenario_has_diverse_vpp_types(monkeypatch):
    sim = _load(monkeypatch)
    vpps = list(sim.vpps.values())

    wind = [v for v in vpps if v.params.wind_kw_rated > 0]
    gas = [v for v in vpps if isinstance(v.agent, GasGeneratorAgent)]
    industrial = [v for v in vpps if v.params.load_profile == "industrial"]
    commercial = [v for v in vpps if v.params.load_profile == "commercial"]
    flat = [v for v in vpps if v.params.load_profile == "flat"]

    assert len(wind) >= 5, "expected several wind farms"
    assert all(v.wind is not None for v in wind), "wind VPPs must carry a WindTurbine"
    assert len(gas) >= 3, "expected several gas generators"
    assert all(v.params.gas_kw_max > 0 for v in gas)
    assert len(industrial) >= 4, "expected several factories"
    assert commercial and flat


def test_scenario_strips_site_coords_when_real_weather_disabled(monkeypatch):
    sim = _load(monkeypatch)  # EFLUX_PV_PHYSICAL=false in _load
    # No VPP should have ended up with site coords → no weather fetch attempted.
    assert all(v.params.pv_lat is None for v in sim.vpps.values())
    assert all(v.pv.physical_model is None for v in sim.vpps.values())
