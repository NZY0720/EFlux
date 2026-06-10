"""Unit tests for built-in demo scenarios."""

from __future__ import annotations

from eflux.agents.reflective import ReflectiveAgent
from eflux.bridge.bus import InMemoryBus
from eflux.config import get_settings
from eflux.simulator.runner import Simulator
from eflux.simulator.scenarios import load_default_scenario


def test_default_scenario_loads_ten_ordinary_and_one_my_llm_agent(monkeypatch):
    monkeypatch.setenv("EFLUX_PV_PHYSICAL", "false")
    monkeypatch.setenv("EFLUX_REFLECTIVE_ENABLED", "false")
    get_settings.cache_clear()

    sim = Simulator(bus=InMemoryBus())
    load_default_scenario(sim)

    assert len(sim.vpps) == 11
    my_vpps = sim.my_managed_vpps()
    assert len(my_vpps) == 1
    assert my_vpps[0].name == "my-llm-vpp"
    assert isinstance(my_vpps[0].agent, ReflectiveAgent)
    assert len([v for v in sim.vpps.values() if not v.is_my_vpp]) == 10
