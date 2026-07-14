"""Adapters for indispensable persisted data that predates the V1 contract reset."""

from __future__ import annotations


def normalize_managed_config_v0(config: dict) -> tuple[str, bool]:
    """Translate one persisted pre-reset managed-agent row into the V1 split model."""

    raw = config.get("algorithm")
    stored = config.get("llm_enabled")
    if raw is None or raw == "hybrid":
        return "ppo", True if stored is None else bool(stored)
    algorithm = "truthful" if raw == "zi" else str(raw)
    if algorithm not in {"ppo", "scripted", "truthful", "zip", "gd", "aa"}:
        algorithm = "ppo"
    return algorithm, bool(stored) if stored is not None else False
