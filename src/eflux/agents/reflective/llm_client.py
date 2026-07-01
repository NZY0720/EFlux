"""Thin async wrapper around an OpenAI-compatible chat-completions endpoint.

Small surface intentionally: just `chat()` and `aclose()`. Caller (the prompt module)
owns message construction and response parsing.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_sec: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    @property
    def model(self) -> str:
        return self._model

    async def chat(
        self, messages: list[dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 4096
    ) -> str:
        """POST /chat/completions, return the assistant content string."""
        url = f"{self._base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            # Reflection hints are a tiny JSON blob, but reasoning models spend
            # most of the budget thinking before emitting content — too small a
            # cap yields an empty completion. The default bounds runaway responses
            # while leaving room to reason; callers (e.g. chat) can lower it.
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }
        resp = await self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        # OpenAI-compatible: choices[0].message.content
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected LLM response shape: {data!r}") from e

    async def aclose(self) -> None:
        await self._client.aclose()
