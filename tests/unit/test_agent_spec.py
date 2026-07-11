"""Unit tests for the AgentSpec participant schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eflux.simulator.agent_spec import (
    AgentSpec,
    agent_spec_json_schema,
    validate_vpp_params,
)
from eflux.vpp.base import VPPParams


def test_minimal_spec_defaults_to_truthful():
    spec = AgentSpec.model_validate({"name": "vpp-1"})
    assert spec.agent == "truthful"
    assert spec.params == {}
    assert spec.agent_params == {}
    assert spec.persona is None


def test_unknown_agent_kind_rejected():
    with pytest.raises(ValidationError, match="agent"):
        AgentSpec.model_validate({"name": "vpp-1", "agent": "quantum"})


def test_classical_baseline_kinds_accepted():
    for kind in ("zip", "gd", "aa"):
        spec = AgentSpec.model_validate({"name": f"vpp-{kind}", "agent": kind})
        assert spec.agent == kind


def test_unknown_top_level_key_rejected():
    # extra="forbid" — a YAML typo like 'parms' must fail loudly, not load silently.
    with pytest.raises(ValidationError, match="parms"):
        AgentSpec.model_validate({"name": "vpp-1", "parms": {"pv_kw_peak": 3.0}})


def test_persona_only_valid_for_llm_managed_agents():
    persona = {"name": "arb", "prompt": "Trade the spread aggressively."}
    with pytest.raises(ValidationError, match="persona"):
        AgentSpec.model_validate({"name": "vpp-1", "agent": "truthful", "persona": persona})
    spec = AgentSpec.model_validate(
        {"name": "vpp-1", "agent": "hybrid", "persona": persona}
    )
    assert spec.persona is not None and spec.persona.name == "arb"
    legacy = AgentSpec.model_validate(
        {"name": "vpp-2", "agent": "reflective", "persona": persona}
    )
    assert legacy.persona is not None and legacy.persona.name == "arb"


def test_bad_params_type_rejected_at_spec_parse():
    with pytest.raises(ValidationError):
        AgentSpec.model_validate({"name": "vpp-1", "params": {"pv_kw_peak": "lots"}})


def test_validate_vpp_params_round_trip_matches_from_dict():
    sparse = {"pv_kw_peak": 8.0, "battery_kwh": 15.0, "load_profile": "industrial"}
    assert validate_vpp_params(sparse) == VPPParams.from_dict(sparse).to_dict()


def test_validate_vpp_params_rejects_unknown_keys():
    # A typo like 'batery_kwh' must fail loudly — silently falling back to the
    # default battery would skew every capacity-derived calculation.
    with pytest.raises(ValueError, match="frobnicate"):
        validate_vpp_params({"pv_kw_peak": 2.0, "frobnicate": True})


def test_spec_params_typo_rejected_at_parse():
    with pytest.raises(ValidationError, match="batery_kwh"):
        AgentSpec.model_validate({"name": "vpp-1", "params": {"batery_kwh": 500}})


def test_roster_params_are_coerced_not_raw(monkeypatch, tmp_path):
    """YAML string numerics ("12") must reach VPPParams as floats — the raw
    from_dict path stored them verbatim and crashed mid-run on first use."""
    from eflux.bridge.bus import InMemoryBus
    from eflux.config import get_settings
    from eflux.simulator.runner import Simulator
    from eflux.simulator.scenarios import load_default_scenario

    scenario = tmp_path / "scenario.yaml"
    scenario.write_text(
        """
vpps:
  - name: quoted-numbers
    agent: truthful
    params: { battery_kwh: "12", pv_kw_peak: "3.5" }
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EFLUX_SCENARIO_FILE", str(scenario))
    monkeypatch.setenv("EFLUX_PV_PHYSICAL", "false")
    monkeypatch.setenv("EFLUX_REFLECTIVE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        sim = Simulator(bus=InMemoryBus())
        load_default_scenario(sim)
    finally:
        get_settings.cache_clear()

    vpp = next(v for v in sim.vpps.values() if v.name == "quoted-numbers")
    assert vpp.params.battery_kwh == 12.0 and isinstance(vpp.params.battery_kwh, float)
    assert vpp.params.pv_kw_peak == 3.5 and isinstance(vpp.params.pv_kw_peak, float)


def test_agent_params_reach_agent_constructor(monkeypatch, tmp_path):
    from eflux.bridge.bus import InMemoryBus
    from eflux.config import get_settings
    from eflux.simulator.runner import Simulator
    from eflux.simulator.scenarios import load_default_scenario

    scenario = tmp_path / "scenario.yaml"
    scenario.write_text(
        """
vpps:
  - name: gas-custom
    agent: gas
    agent_params: { min_qty: 0.02 }
    params: { gas_kw_max: 10.0, pv_kw_peak: 0.0, battery_kwh: 0.0, battery_kw_max: 0.0, load_kw_base: 0.0 }
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EFLUX_SCENARIO_FILE", str(scenario))
    monkeypatch.setenv("EFLUX_PV_PHYSICAL", "false")
    monkeypatch.setenv("EFLUX_REFLECTIVE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        sim = Simulator(bus=InMemoryBus())
        load_default_scenario(sim)
    finally:
        get_settings.cache_clear()

    gas = next(v for v in sim.vpps.values() if v.name == "gas-custom")
    assert gas.agent.min_qty == 0.02


def test_duplicate_names_rejected(monkeypatch, tmp_path):
    from eflux.bridge.bus import InMemoryBus
    from eflux.config import get_settings
    from eflux.simulator.runner import Simulator
    from eflux.simulator.scenarios import load_default_scenario

    scenario = tmp_path / "scenario.yaml"
    scenario.write_text(
        """
vpps:
  - name: twin
    agent: truthful
  - name: twin
    agent: truthful
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EFLUX_SCENARIO_FILE", str(scenario))
    monkeypatch.setenv("EFLUX_PV_PHYSICAL", "false")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="duplicate"):
            sim = Simulator(bus=InMemoryBus())
            load_default_scenario(sim)
    finally:
        get_settings.cache_clear()


def test_json_schema_export_contains_contract_fields():
    schema = agent_spec_json_schema()
    assert "agent" in schema["properties"]
    assert "persona" in schema["properties"]
    # params is expanded to the full VPPParams field schema.
    assert "pv_kw_peak" in schema["properties"]["params"]["properties"]
    assert "gas_cost_per_mwh" in schema["properties"]["params"]["properties"]


def test_executor_legacy_ppo_kind_rejected():
    # The legacy RLlib `ppo` executor was removed; only scripted / ppo_online remain.
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(
            {"name": "s", "agent": "strategy", "executor": {"kind": "ppo", "checkpoint": "ck"}}
        )


def test_executor_only_valid_for_strategy_or_hybrid():
    with pytest.raises(ValidationError, match="executor is only valid"):
        AgentSpec.model_validate(
            {"name": "z", "agent": "truthful", "executor": {"kind": "ppo_online", "checkpoint": "ck"}}
        )


def test_executor_ppo_online_accepted_on_strategy_and_hybrid():
    for kind in ("strategy", "hybrid"):
        spec = AgentSpec.model_validate(
            {"name": f"{kind}-1", "agent": kind, "executor": {"kind": "ppo_online", "checkpoint": "checkpoints/x"}}
        )
        assert spec.executor.kind == "ppo_online" and spec.executor.checkpoint == "checkpoints/x"


def test_executor_defaults_to_scripted_and_is_optional():
    assert AgentSpec.model_validate({"name": "s", "agent": "strategy"}).executor is None
    spec = AgentSpec.model_validate({"name": "s", "agent": "strategy", "executor": {}})
    assert spec.executor.kind == "scripted"
