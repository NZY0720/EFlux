"""Canonical provenance manifests for reproducible EFlux runs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from eflux import __version__
from eflux.config import PROJECT_ROOT


def canonical_json(value: Any) -> str:
    """Stable JSON encoding used by every evidence hash."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def engine_commit() -> str:
    """Return the immutable build identity when available, without requiring git."""

    configured = os.environ.get("EFLUX_BUILD_COMMIT", "").strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        value = result.stdout.strip()
        return value or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def source_dirty() -> bool:
    """Make uncommitted development executions visibly non-release artifacts."""

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def engine_source_sha256() -> str:
    """Fingerprint the executable Python tree, including untracked development files."""

    digest = hashlib.sha256()
    paths = [*sorted((PROJECT_ROOT / "src" / "eflux").rglob("*.py")), PROJECT_ROOT / "pyproject.toml"]
    for path in paths:
        if not path.is_file():
            continue
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class DataArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str
    resolution: str
    sha256: str
    rows: int | None = None
    start: datetime | None = None
    end: datetime | None = None


class RunManifest(BaseModel):
    """Portable evidence identity shared by prove-out, evaluation and backtests."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    run_type: Literal["proveout", "evaluation", "backtest", "comparison"]
    engine_version: str = __version__
    engine_commit: str = Field(default_factory=engine_commit)
    engine_source_sha256: str = Field(default_factory=engine_source_sha256)
    source_dirty: bool = Field(default_factory=source_dirty)
    protocol_version: int = 2
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    market_mode: str
    rules_version: str | None = None
    scenario_sha256: str | None = None
    config_sha256: str
    seed_labels: list[str] = Field(default_factory=list)
    data: list[DataArtifact] = Field(default_factory=list)
    model_sha256: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @property
    def evidence_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"created_at"})
        return content_sha256(payload)


def build_manifest(
    *,
    run_type: Literal["proveout", "evaluation", "backtest", "comparison"],
    market_mode: str,
    parameters: dict[str, Any],
    rules_version: str | None = None,
    scenario_sha256: str | None = None,
    seed_labels: list[str] | None = None,
    data: list[DataArtifact] | None = None,
    model_sha256: dict[str, str] | None = None,
) -> RunManifest:
    return RunManifest(
        run_type=run_type,
        market_mode=market_mode,
        rules_version=rules_version,
        scenario_sha256=scenario_sha256,
        config_sha256=content_sha256(parameters),
        seed_labels=list(seed_labels or ()),
        data=list(data or ()),
        model_sha256=dict(model_sha256 or {}),
        parameters=parameters,
    )
