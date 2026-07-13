"""Versioned, strict and hashable scenario contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from eflux.config import PROJECT_ROOT
from eflux.simulator.agent_spec import AgentSpec


class ScenarioSpecV1(BaseModel):
    """A complete experiment roster; participant order is semantically significant."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    market_mode: Literal["p2p", "realprice", "hybrid", "any"] = "any"
    participants: list[AgentSpec] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_participant_names(self) -> ScenarioSpecV1:
        names = [participant.name for participant in self.participants]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate participant names: {duplicates}")
        return self

    @property
    def semantic_sha256(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(encoded.encode()).hexdigest()


def resolve_scenario_path(path: Path | str) -> Path:
    resolved = Path(path)
    return resolved if resolved.is_absolute() else PROJECT_ROOT / resolved


def load_scenario_spec(path: Path | str, *, allow_legacy: bool = True) -> ScenarioSpecV1:
    resolved = resolve_scenario_path(path)
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Scenario file {resolved} must contain a YAML object")
    if "vpps" in raw:
        if not allow_legacy:
            raise ValueError("legacy 'vpps' scenarios must be normalized to ScenarioSpec v1")
        unknown = sorted(set(raw) - {"vpps"})
        if unknown:
            raise ValueError(f"legacy scenario contains unknown top-level keys: {unknown}")
        raw = {
            "schema_version": "1",
            "name": resolved.stem,
            "market_mode": "any",
            "participants": raw["vpps"],
            "metadata": {"legacy_adapter": True},
        }
    return ScenarioSpecV1.model_validate(raw)


def normalized_scenario_yaml(spec: ScenarioSpecV1) -> str:
    return yaml.safe_dump(
        spec.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
    )
