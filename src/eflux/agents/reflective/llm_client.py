"""Thin async wrapper around an OpenAI-compatible chat-completions endpoint.

Small surface intentionally: just `chat()` and `aclose()`. Caller (the prompt module)
owns message construction and response parsing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


class LLMBudgetExceeded(RuntimeError):
    """Raised before a call that could breach the configured spend ceiling."""


@dataclass(frozen=True, slots=True)
class _BudgetReservation:
    projected_cost_usd: float
    estimated_prompt_tokens: int
    max_completion_tokens: int


class LLMUsageBudget:
    """Shared token/cost meter with conservative pre-call reservation.

    Provider bills vary, so rates are explicit configuration.  The estimate is
    intentionally conservative: before sending a request we reserve estimated
    prompt tokens plus the full completion allowance.  A fleet therefore cannot
    race several requests past the global USD ceiling.
    """

    def __init__(
        self,
        *,
        max_cost_usd: float,
        input_cost_per_million_tokens: float,
        output_cost_per_million_tokens: float,
    ) -> None:
        if (
            min(
                max_cost_usd,
                input_cost_per_million_tokens,
                output_cost_per_million_tokens,
            )
            < 0
        ):
            raise ValueError("LLM budget and token rates must be non-negative")
        self.max_cost_usd = float(max_cost_usd)
        self.input_rate = float(input_cost_per_million_tokens)
        self.output_rate = float(output_cost_per_million_tokens)
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0
        self.estimated_cost_usd = 0.0
        self._reserved_cost_usd = 0.0
        self._lock = asyncio.Lock()

    async def reserve(
        self, *, estimated_prompt_tokens: int, max_completion_tokens: int
    ) -> _BudgetReservation:
        projected = self._cost(estimated_prompt_tokens, max_completion_tokens)
        async with self._lock:
            projected_total = self.estimated_cost_usd + self._reserved_cost_usd + projected
            if projected_total > self.max_cost_usd + 1e-12:
                raise LLMBudgetExceeded(
                    f"LLM budget exhausted: projected ${projected_total:.4f} "
                    f"exceeds ${self.max_cost_usd:.2f} ceiling"
                )
            self._reserved_cost_usd += projected
        return _BudgetReservation(projected, estimated_prompt_tokens, max_completion_tokens)

    async def settle(
        self,
        reservation: _BudgetReservation,
        *,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        async with self._lock:
            self._reserved_cost_usd = max(
                0.0, self._reserved_cost_usd - reservation.projected_cost_usd
            )
            self.prompt_tokens += max(0, int(prompt_tokens))
            self.completion_tokens += max(0, int(completion_tokens))
            self.calls += 1
            self.estimated_cost_usd += self._cost(prompt_tokens, completion_tokens)

    async def release(self, reservation: _BudgetReservation) -> None:
        async with self._lock:
            self._reserved_cost_usd = max(
                0.0, self._reserved_cost_usd - reservation.projected_cost_usd
            )

    def snapshot(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "reserved_cost_usd": round(self._reserved_cost_usd, 6),
            "max_cost_usd": self.max_cost_usd,
            "remaining_cost_usd": round(
                max(0.0, self.max_cost_usd - self.estimated_cost_usd - self._reserved_cost_usd),
                6,
            ),
        }

    def _cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            max(0, int(prompt_tokens)) * self.input_rate
            + max(0, int(completion_tokens)) * self.output_rate
        ) / 1_000_000.0


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_sec: float = 30.0,
        budget: LLMUsageBudget | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._budget = budget
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    @property
    def model(self) -> str:
        return self._model

    @property
    def usage(self) -> dict[str, float | int] | None:
        return None if self._budget is None else self._budget.snapshot()

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
        estimated_prompt_tokens = max(
            1,
            (sum(len(str(message.get("content", ""))) for message in messages) + 2) // 3,
        )
        reservation = None
        if self._budget is not None:
            reservation = await self._budget.reserve(
                estimated_prompt_tokens=estimated_prompt_tokens,
                max_completion_tokens=max_tokens,
            )
        try:
            resp = await self._client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            if reservation is not None:
                await self._budget.release(reservation)
            raise
        # OpenAI-compatible: choices[0].message.content
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            if reservation is not None:
                await self._budget.release(reservation)
            raise RuntimeError(f"Unexpected LLM response shape: {data!r}") from e
        if reservation is not None:
            usage = data.get("usage") or {}
            prompt_tokens = int(
                usage.get("prompt_tokens", usage.get("input_tokens", estimated_prompt_tokens))
            )
            completion_tokens = int(
                usage.get(
                    "completion_tokens",
                    usage.get("output_tokens", max(1, (len(str(content)) + 2) // 3)),
                )
            )
            await self._budget.settle(
                reservation,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        return content

    async def aclose(self) -> None:
        await self._client.aclose()
