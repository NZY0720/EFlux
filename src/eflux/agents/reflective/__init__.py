"""Reflective LLM agent — periodically asks an LLM for strategy hints.

The agent wraps a deterministic baseline (default: TruthfulAgent) and every N ticks
fires an async "reflection" prompt to the LLM. The LLM returns a small JSON object
with price/qty adjustments, which the agent applies to the baseline's intents.
"""

from __future__ import annotations

from eflux.agents.reflective.agent import ReflectiveAgent

__all__ = ["ReflectiveAgent"]
