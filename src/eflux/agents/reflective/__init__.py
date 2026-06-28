"""Shared LLM strategist stack for the hybrid agents.

Despite the historical package name, this no longer contains the old standalone
``ReflectiveAgent``. It holds the slow strategist layer that ``HybridPolicyAgent``
consumes:

- ``strategist`` — ``LLMStrategist``/``StaticStrategist`` and ``StrategyGuidance``.
- ``pool`` — ``SharedLLM``: one validated LLM connection shared by every managed VPP.
- ``llm_client`` — the OpenAI-compatible async chat client the strategist calls.

Import directly from the submodules; nothing is re-exported here.
"""

from __future__ import annotations

__all__: list[str] = []
