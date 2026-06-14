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


def test_load_ppo_scenario_skips_gracefully_on_bad_checkpoint(monkeypatch):
    """A bogus EFLUX_PPO_CHECKPOINT must not crash startup — whether the 'ai'
    extras are missing (ImportError) or the checkpoint fails to load."""
    from eflux.simulator.scenarios import load_ppo_scenario

    monkeypatch.setenv("EFLUX_PV_PHYSICAL", "false")
    get_settings.cache_clear()
    sim = Simulator(bus=InMemoryBus())

    load_ppo_scenario(sim, "/nonexistent/checkpoint/path")

    assert len(sim.vpps) == 0


def test_default_scenario_loads_thirty_three_vpps_incl_llm_fleet(monkeypatch):
    sim = _load(monkeypatch)

    assert len(sim.vpps) == 33
    my_vpps = sim.my_managed_vpps()
    assert len(my_vpps) == 4
    assert my_vpps[0].name == "my-llm-vpp"
    assert {v.name for v in my_vpps} == {
        "my-llm-vpp",
        "llm-arb-aggressive",
        "llm-wind-conservative",
        "llm-demand-buyer",
    }
    assert all(isinstance(v.agent, ReflectiveAgent) for v in my_vpps)
    assert len([v for v in sim.vpps.values() if not v.is_my_vpp]) == 29


def test_llm_fleet_shares_connection_and_staggers_reflections(monkeypatch):
    """All reflective agents must share one LLM client + gate (single slow
    endpoint), with distinct evenly-spread reflection offsets so they never
    trigger on the same tick."""
    sim = _load(monkeypatch)
    agents = [v.agent for v in sim.my_managed_vpps()]

    offsets = [a.reflect_offset_ticks for a in agents]
    assert len(set(offsets)) == len(agents), f"offsets must be distinct, got {offsets}"
    assert all(0 <= o < a.reflect_every_n_ticks for o, a in zip(offsets, agents, strict=True))

    gates = {id(a.llm_gate) for a in agents}
    assert len(gates) == 1, "all reflective agents must share the same gate"
    # Reflective disabled in _load → shared client is None everywhere.
    assert all(a.llm_client is None for a in agents)


def test_llm_personas_reach_agents(monkeypatch):
    sim = _load(monkeypatch)
    by_name = {v.name: v.agent for v in sim.my_managed_vpps()}

    assert by_name["my-llm-vpp"].persona_prompt is None  # no persona declared
    assert "arbitrageur" in by_name["llm-arb-aggressive"].persona_prompt
    assert "wind farm" in by_name["llm-wind-conservative"].persona_prompt
    assert "Minimize cost" in by_name["llm-demand-buyer"].persona_prompt
    # demand-side personas carry price-responsive inner agents
    assert by_name["llm-demand-buyer"].inner.demand_beta == 0.5


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


def test_cost_diversification_spreads_price_ref_excluding_llm(monkeypatch):
    """Non-LLM truthful/ZI agents get a deterministic per-agent price_ref jitter
    so their cost levels fan out; reflective (LLM) agents are left at the default."""
    from eflux.agents.truthful import TruthfulAgent
    from eflux.agents.zi import ZIAgent

    sim = _load(monkeypatch)

    price_refs = [
        float(v.agent.price_ref)
        for v in sim.vpps.values()
        if type(v.agent) in (TruthfulAgent, ZIAgent)
    ]
    assert len(price_refs) >= 5
    assert len(set(price_refs)) > 1, "jitter should spread price_ref off the flat 50"
    assert all(46.9 < p < 53.1 for p in price_refs), "stay within ±6% of 50"
    assert any(abs(p - 50.0) > 1e-6 for p in price_refs)

    # LLM (reflective) agents excluded → inner truthful keeps the 50.0 default.
    llm = [v.agent for v in sim.my_managed_vpps()]
    assert llm and all(isinstance(a, ReflectiveAgent) for a in llm)
    assert all(float(a.inner.price_ref) == 50.0 for a in llm)


def test_cost_diversification_is_deterministic_across_loads(monkeypatch):
    """Same roster + seed ⇒ identical jittered price_refs (stable across restarts)."""
    from eflux.agents.truthful import TruthfulAgent
    from eflux.agents.zi import ZIAgent

    def refs() -> dict[str, float]:
        sim = _load(monkeypatch)
        return {
            v.name: float(v.agent.price_ref)
            for v in sim.vpps.values()
            if type(v.agent) in (TruthfulAgent, ZIAgent)
        }

    assert refs() == refs()


def test_scenario_strips_site_coords_when_real_weather_disabled(monkeypatch):
    sim = _load(monkeypatch)  # EFLUX_PV_PHYSICAL=false in _load
    # No VPP should have ended up with site coords → no weather fetch attempted.
    assert all(v.params.pv_lat is None for v in sim.vpps.values())
    assert all(v.pv.physical_model is None for v in sim.vpps.values())
