"""AgentSpec — the one schema a market participant is declared with.

Both entry paths into the market validate against this module:
- the built-in YAML roster (scenarios/*.yaml), parsed entry-by-entry as AgentSpec;
- external VPPs via POST /vpps, whose `params` block goes through the same
  validate_vpp_params() helper.

Export the machine-readable contract with `eflux agent-spec-schema` (JSON Schema),
documented for external integrators in docs/AGENT_SPEC.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from eflux.vpp.base import VPPParams

# Pydantic validates the (frozen) dataclass fields with real type checks —
# VPPParams.from_dict alone would silently store e.g. a string pv_kw_peak.
_VPP_PARAMS_ADAPTER: TypeAdapter[VPPParams] = TypeAdapter(VPPParams)


def validate_vpp_params(d: dict) -> dict:
    """Validate a sparse VPPParams dict and return the normalized full dict.

    Unknown keys are rejected — a typo like 'batery_kwh' would otherwise
    silently fall back to the default and skew every capacity-derived
    calculation. Known keys are type-checked (and coerced, e.g. "12" → 12.0).
    Raises ValueError (pydantic.ValidationError for bad values) — shared by the
    YAML roster loader and the POST /vpps endpoint so internal and external
    participants live under one schema.
    """
    known = set(VPPParams.__dataclass_fields__)
    unknown = sorted(set(d) - known)
    if unknown:
        raise ValueError(f"unknown params keys: {', '.join(unknown)}")
    defaults = VPPParams()
    merged = {f: d.get(f, getattr(defaults, f)) for f in known}
    validated = _VPP_PARAMS_ADAPTER.validate_python(merged)
    # Gas providers are pure dispatchable supply: fuel capacity is metered onto the sell
    # side and settles through fuel, not through ambient generation, load, or storage.
    if validated.gas_kw_max > 0 and (
        validated.pv_kw_peak > 0
        or validated.wind_kw_rated > 0
        or validated.load_kw_base > 0
        or validated.battery_kwh > 0
        or validated.battery_kw_max > 0
    ):
        raise ValueError(
            "gas_kw_max > 0 requires pv_kw_peak = wind_kw_rated = load_kw_base = "
            "battery_kwh = battery_kw_max = 0 "
            "(gas providers are pure dispatchable supply)"
        )
    return validated.to_dict()


class PersonaSpec(BaseModel):
    """Strategy persona for LLM-managed agents."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    # Appended to the LLM strategist prompt; keep it a compact strategy brief.
    prompt: str = Field(min_length=1, max_length=600)


class ExecutorSpec(BaseModel):
    """Tactical executor (the policy that selects each StrategyAction) for strategy /
    hybrid agents. `scripted` (default) uses the deterministic baseline; `ppo_online` loads
    the custom live-learning torch PPO policy (warm-started from a BC/online checkpoint) that
    updates during the sim. A missing checkpoint / 'ai' extras falls back to scripted at load
    (never crashes startup)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["scripted", "ppo_online"] = "scripted"
    checkpoint: str | None = None  # optional warm-start for ppo_online
    # ppo_online only: whether the policy updates live. False = serve the warm-started net
    # frozen (still the custom torch policy, just no gradient steps).
    online_learning: bool = True


class AgentSpec(BaseModel):
    """One market participant: identity, assets (VPPParams) and strategy."""

    model_config = ConfigDict(extra="forbid")  # catch YAML typos loudly

    name: str = Field(min_length=1, max_length=100)
    # `zip`/`gd`/`aa` are the classical quantitative baselines (continuous double auction).
    agent: Literal["truthful", "gas", "strategy", "hybrid", "zip", "gd", "aa"] = (
        "truthful"
    )
    seed: int | None = None
    # DER portfolio — sparse VPPParams fields (see validate_vpp_params).
    params: dict = Field(default_factory=dict)
    # Constructor kwargs for the strategy class. For "hybrid", they go to
    # HybridPolicyAgent — e.g. {demand_beta: 0.5}.
    agent_params: dict = Field(default_factory=dict)
    persona: PersonaSpec | None = None
    # Tactical policy for strategy/hybrid agents (scripted default, or learned PPO).
    executor: ExecutorSpec | None = None
    # Hybrid only: also spawn a strategist-less PPO twin (a StrategyAgent with the same
    # executor/params/seed, name suffix "-ppo-mirror") into the same market, so the
    # LLM-coached agent and its PPO-only control trade side-by-side for A/B attribution.
    mirror: bool = False

    @model_validator(mode="after")
    def _check(self) -> AgentSpec:
        if self.persona is not None and self.agent != "hybrid":
            raise ValueError(
                f"{self.name!r}: persona is only valid for agent: hybrid (got {self.agent!r})"
            )
        if self.mirror and self.agent != "hybrid":
            raise ValueError(
                f"{self.name!r}: mirror is only valid for agent: hybrid (got {self.agent!r})"
            )
        if self.executor is not None and self.agent not in ("strategy", "hybrid"):
            raise ValueError(
                f"{self.name!r}: executor is only valid for agent: strategy/hybrid "
                f"(got {self.agent!r})"
            )
        # Type-check the params block now so a bad roster fails at load, not mid-run.
        validate_vpp_params(self.params)
        return self


def agent_spec_json_schema() -> dict:
    """JSON Schema for AgentSpec with the `params` block expanded to the full
    VPPParams field schema — the contract external VPPs integrate against."""
    schema = AgentSpec.model_json_schema()
    params_schema = _VPP_PARAMS_ADAPTER.json_schema()
    params_schema["description"] = (
        "DER portfolio (VPPParams). All fields optional; unknown keys are rejected."
    )
    schema["properties"]["params"] = params_schema
    return schema
