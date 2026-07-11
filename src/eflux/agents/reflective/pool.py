"""SharedLLM — one LLM connection shared by every LLM-managed agent.

The roster can declare several `agent: hybrid` VPPs, but there is a single configured
endpoint (and it is slow — reasoning models take up to two minutes per completion).
So the connection is validated once at startup, one client is shared, and a
Semaphore(1) gate guarantees at most one in-flight strategist call across all agents.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from eflux.agents.reflective.llm_client import LLMClient, LLMUsageMeter

log = logging.getLogger(__name__)


# Curated subset of the provider's catalogue offered for managed-agent (Tier-0)
# deployment. Single source of truth for the /vpps/models endpoint + validation.
CURATED_MODELS: tuple[str, ...] = (
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "glm-5.2",
    "qwen3.7-max",
    "mimo-v2-pro",
    "minimax-m3",
    "kimi-k2.7-code",
)


@dataclass
class SharedLLM:
    client: LLMClient | None
    status: str  # human-readable, surfaced as llm_status on each managed VPP
    strategy_suffix: str  # e.g. "opencode:deepseek-v4-pro" or "offline fallback"
    gate: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    # Connection params retained so per-model clients can be built lazily — all sharing
    # the same endpoint/key + the single in-flight gate (Tier-0 per-agent model choice).
    base_url: str | None = None
    api_key: str | None = None
    timeout_sec: float = 120.0
    default_model: str | None = None
    usage_meter: LLMUsageMeter | None = None
    _pool: dict[str, LLMClient] = field(default_factory=dict)

    @property
    def live(self) -> bool:
        return self.client is not None

    def client_for(self, model: str | None) -> LLMClient | None:
        """An LLMClient for `model` (or the default), or None if no LLM is configured.
        Per-model clients reuse base_url/api_key and the one-in-flight gate, so picking a
        different model never opens a second concurrent lane to the endpoint."""
        if self.client is None or self.base_url is None or self.api_key is None:
            return None
        if not model or model == self.default_model:
            return self.client
        existing = self._pool.get(model)
        if existing is None:
            existing = LLMClient(
                base_url=self.base_url,
                api_key=self.api_key,
                model=model,
                timeout_sec=self.timeout_sec,
                usage_meter=self.usage_meter,
            )
            self._pool[model] = existing
        return existing

    @classmethod
    def from_settings(cls, settings) -> SharedLLM:
        """Build the shared connection from app settings, validating it once.

        Falls back to client=None (agents run their inner baseline) with a
        status string explaining exactly what is missing or failing.
        """
        api_key = settings.llm_api_key
        if settings.reflective_enabled and api_key and settings.llm_base_url and settings.llm_model:
            ok, detail = validate_llm_connection(
                base_url=settings.llm_base_url,
                api_key=api_key,
                model=settings.llm_model,
            )
            if ok:
                usage_meter = LLMUsageMeter(
                    input_cost_per_million_tokens=settings.llm_input_cost_per_million_tokens,
                    output_cost_per_million_tokens=settings.llm_output_cost_per_million_tokens,
                )
                client = LLMClient(
                    base_url=settings.llm_base_url,
                    api_key=api_key,
                    model=settings.llm_model,
                    timeout_sec=settings.llm_timeout_sec,
                    usage_meter=usage_meter,
                )
                return cls(
                    client=client,
                    status=f"live LLM strategist via {settings.llm_base_url}",
                    strategy_suffix=f"{settings.llm_provider}:{settings.llm_model}",
                    base_url=settings.llm_base_url,
                    api_key=api_key,
                    timeout_sec=settings.llm_timeout_sec,
                    default_model=settings.llm_model,
                    usage_meter=usage_meter,
                )
            log.warning("LLM connection check failed: %s", detail)
            return cls(
                client=None,
                status=f"offline fallback: {detail}",
                strategy_suffix="offline fallback",
            )

        missing = []
        if not settings.reflective_enabled:
            missing.append("EFLUX_REFLECTIVE_ENABLED=false")
        if not api_key:
            missing.append("missing key.txt")
        if not settings.llm_base_url:
            missing.append("missing EFLUX_LLM_BASE_URL")
        if not settings.llm_model:
            missing.append("missing EFLUX_LLM_MODEL")
        status = "offline fallback: " + ", ".join(missing)
        log.warning(
            "LLM-managed agents loaded without live LLM calls (enabled=%s key=%s base_url=%s model=%s)",
            settings.reflective_enabled,
            bool(api_key),
            bool(settings.llm_base_url),
            bool(settings.llm_model),
        )
        return cls(client=None, status=status, strategy_suffix="offline fallback")

    @property
    def usage(self) -> dict[str, float | int] | None:
        return None if self.usage_meter is None else self.usage_meter.snapshot()


def validate_llm_connection(*, base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """Synchronous startup probe of the chat-completions endpoint."""
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with only: ok"}],
                "max_completion_tokens": 8,
                "temperature": 0.2,
                "stream": False,
            },
            timeout=15.0,
        )
        if resp.status_code >= 400:
            try:
                data = resp.json()
                message = data.get("error", {}).get("message") or resp.text[:160]
            except Exception:
                message = resp.text[:160]
            return False, f"{resp.status_code} {message}"
        data = resp.json()
        data["choices"][0]["message"]["content"]
        return True, "ok"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
