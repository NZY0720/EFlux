#!/usr/bin/env python3
"""Verify that every checked-in checkpoint satisfies the canonical V1 manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eflux.agents.ppo.checkpoints import BC_CHECKPOINT_FORMAT, load_checkpoint

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "checkpoints" / "manifest.v1.json"


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1":
        raise SystemExit("checkpoint manifest schema_version must be 1")

    contract = manifest["contract"]
    declared_paths: set[Path] = set()
    for name, artifact in manifest["artifacts"].items():
        path = ROOT / artifact["path"]
        declared_paths.add(path.resolve())
        data = path.read_bytes()
        if len(data) != artifact["size_bytes"]:
            raise SystemExit(f"{name}: checkpoint size does not match manifest")
        if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise SystemExit(f"{name}: checkpoint digest does not match manifest")

        payload = load_checkpoint(path, expected_format=BC_CHECKPOINT_FORMAT)
        expected = {
            "format": contract["format"],
            "encoding_version": contract["encoding_version"],
            "obs_version": contract["observation_version"],
            "obs_dim": contract["observation_dimension"],
            "action_profile": artifact["action_profile"],
        }
        actual = {key: payload.get(key) for key in expected}
        if actual != expected:
            raise SystemExit(f"{name}: checkpoint metadata differs: {actual!r}")

    checked_in = {path.resolve() for path in (ROOT / "checkpoints").glob("*.pt")}
    if checked_in != declared_paths:
        raise SystemExit("checkpoint directory and manifest artifact set differ")
    print(f"verified {len(declared_paths)} V1 checkpoints")


if __name__ == "__main__":
    main()
