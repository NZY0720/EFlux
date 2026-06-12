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
    return _VPP_PARAMS_ADAPTER.validate_python(merged).to_dict()


class PersonaSpec(BaseModel):
    """Strategy persona for LLM-steered (reflective) agents."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    # Appended to the reflective system prompt; keep it a compact strategy brief.
    prompt: str = Field(min_length=1, max_length=600)


class AgentSpec(BaseModel):
    """One market participant: identity, assets (VPPParams) and strategy."""

    model_config = ConfigDict(extra="forbid")  # catch YAML typos loudly

    name: str = Field(min_length=1, max_length=100)
    agent: Literal["zi", "truthful", "gas", "reflective"] = "zi"
    seed: int | None = None
    # DER portfolio — sparse VPPParams fields (see validate_vpp_params).
    params: dict = Field(default_factory=dict)
    # Constructor kwargs for the strategy class (for "reflective", they go to
    # the inner TruthfulAgent — e.g. {demand_beta: 0.5}).
    agent_params: dict = Field(default_factory=dict)
    persona: PersonaSpec | None = None

    @model_validator(mode="after")
    def _check(self) -> AgentSpec:
        if self.persona is not None and self.agent != "reflective":
            raise ValueError(
                f"{self.name!r}: persona is only valid for agent: reflective (got {self.agent!r})"
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
