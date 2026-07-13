"""Train platform-compatible BC warm starts from Decision Trajectory artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def load_policy_samples(path: Path | str) -> tuple[list[list[float]], list[list[float]], dict]:
    """Load consistent, finite policy samples embedded by the EFlux simulator."""

    source = Path(path)
    observations: list[list[float]] = []
    actions: list[list[float]] = []
    metadata: dict[str, Any] | None = None
    with _open_text(source) as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                sample = (row.get("action") or {}).get("policy_sample")
            except (AttributeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid trajectory JSON on line {line_no}") from exc
            if not isinstance(sample, dict):
                continue
            obs = sample.get("observation_vector")
            action = sample.get("action_vector")
            if not isinstance(obs, list) or not isinstance(action, list):
                raise ValueError(f"incomplete policy sample on line {line_no}")
            try:
                obs_row = [float(value) for value in obs]
                action_row = [float(value) for value in action]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"non-numeric policy sample on line {line_no}") from exc
            if (
                not obs_row
                or not action_row
                or not all(math.isfinite(value) for value in (*obs_row, *action_row))
            ):
                raise ValueError(f"non-finite or empty policy sample on line {line_no}")
            current = {
                "encoding_version": int(sample.get("encoding_version", 0)),
                "observation_version": int(sample.get("observation_version", 0)),
                "action_profile": str(sample.get("action_profile", "")),
                "observation_dim": len(obs_row),
                "action_dim": len(action_row),
            }
            if metadata is None:
                metadata = current
            elif current != metadata:
                raise ValueError(
                    f"mixed policy sample schemas on line {line_no}: {current!r} != {metadata!r}"
                )
            observations.append(obs_row)
            actions.append(action_row)
    if metadata is None or len(observations) < 2:
        raise ValueError("trajectory needs at least two platform policy samples for BC")
    return observations, actions, metadata


def train_behavior_clone(
    artifact_path: Path | str,
    output_path: Path | str,
    *,
    epochs: int = 100,
    seed: int = 0,
    market_mode: str,
) -> dict[str, Any]:
    """Train and persist a BC checkpoint that the existing PPO runtime can warm-start."""

    if epochs < 1 or epochs > 10_000:
        raise ValueError("epochs must be between 1 and 10000")
    observations, actions, schema = load_policy_samples(artifact_path)
    try:
        import numpy as np

        from eflux.agents.ppo.bc import (
            mode_accuracy,
            per_mode_recall,
            save_bc,
            trade_mode_accuracy,
            train_bc,
        )
    except ImportError as exc:
        raise RuntimeError("BC training requires the optional eflux[ai] dependencies") from exc

    obs = np.asarray(observations, dtype=np.float32)
    acts = np.asarray(actions, dtype=np.float32)
    net = train_bc(
        obs,
        acts,
        epochs=epochs,
        seed=seed,
        encoding_version=schema["encoding_version"],
        obs_version=schema["observation_version"],
        action_profile=schema["action_profile"],
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_bc(
        net,
        str(target),
        market_mode=market_mode,
        encoding_version=schema["encoding_version"],
        obs_version=schema["observation_version"],
        action_profile=schema["action_profile"],
    )
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "samples": len(obs),
        "epochs": epochs,
        "seed": seed,
        "mode_accuracy": round(mode_accuracy(net, obs, acts), 6),
        "trade_mode_accuracy": round(trade_mode_accuracy(net, obs, acts), 6),
        "per_mode_recall": {
            key: round(value, 6) for key, value in per_mode_recall(net, obs, acts).items()
        },
        "checkpoint_path": str(target),
        "checkpoint_sha256": digest,
        "checkpoint_size_bytes": target.stat().st_size,
        **schema,
    }
