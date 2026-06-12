"""SharedLLM — one LLM connection shared by every reflective agent.

The roster can declare several `agent: reflective` VPPs, but there is a single
configured endpoint (and it is slow — reasoning models take up to two minutes
per completion). So the connection is validated once at startup, one client is
shared, and a Semaphore(1) gate guarantees at most one in-flight call across
all agents — staggered reflection offsets make gate contention rare.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from eflux.agents.reflective.llm_client import LLMClient

log = logging.getLogger(__name__)


@dataclass
class SharedLLM:
    client: LLMClient | None
    status: str  # human-readable, surfaced as llm_status on each managed VPP
    strategy_suffix: str  # e.g. "xiaomi-mimo:mimo-v2.5-pro" or "offline fallback"
    gate: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))

    @property
    def live(self) -> bool:
        return self.client is not None

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
                client = LLMClient(
                    base_url=settings.llm_base_url,
                    api_key=api_key,
                    model=settings.llm_model,
                    timeout_sec=settings.llm_timeout_sec,
                )
                return cls(
                    client=client,
                    status=f"live LLM reflection via {settings.llm_base_url}",
                    strategy_suffix=f"{settings.llm_provider}:{settings.llm_model}",
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
            "Reflective agents loaded without live LLM calls (enabled=%s key=%s base_url=%s model=%s)",
            settings.reflective_enabled,
            bool(api_key),
            bool(settings.llm_base_url),
            bool(settings.llm_model),
        )
        return cls(client=None, status=status, strategy_suffix="offline fallback")


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
