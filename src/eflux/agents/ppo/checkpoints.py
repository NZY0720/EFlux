"""Canonical V1 checkpoint envelope and validation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import torch

from eflux.agents.ppo.primitive_encoding import ENCODING_V1, OBS_DIM_V1, OBS_V1

BC_CHECKPOINT_FORMAT = "bc_primitive_v1"
ONLINE_CHECKPOINT_FORMAT = "online_actor_v1"
CHECKPOINT_FORMATS = frozenset({BC_CHECKPOINT_FORMAT, ONLINE_CHECKPOINT_FORMAT})


def load_checkpoint(
    path: str | Path,
    *,
    map_location: str = "cpu",
    expected_format: str | None = None,
) -> dict[str, Any]:
    """Load and validate a current checkpoint envelope.

    Pre-reset bare state dicts and old protocol metadata intentionally fail closed.
    """

    raw = torch.load(str(path), map_location=map_location, weights_only=True)
    if not isinstance(raw, dict) or not isinstance(raw.get("state_dict"), Mapping):
        raise ValueError("checkpoint must use the EFlux V1 envelope")
    checkpoint_format = raw.get("format")
    if checkpoint_format not in CHECKPOINT_FORMATS:
        raise ValueError(f"unsupported checkpoint format: {checkpoint_format!r}")
    if expected_format is not None and checkpoint_format != expected_format:
        raise ValueError(
            f"checkpoint format {checkpoint_format!r} does not match {expected_format!r}"
        )
    if raw.get("encoding_version") != ENCODING_V1:
        raise ValueError("checkpoint encoding_version must be 1")
    if raw.get("obs_version") != OBS_V1 or raw.get("obs_dim") != OBS_DIM_V1:
        raise ValueError("checkpoint observation schema must be V1/33")
    return raw


def checkpoint_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "state_dict"}
