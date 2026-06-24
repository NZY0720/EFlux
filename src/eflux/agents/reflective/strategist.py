"""Structured LLM guidance — the slow strategist layer (design note §5.1).

The LLM operates over a longer horizon than the tactical policy: it reads the regime,
reviews the last window, and recommends/discourages strategy primitives, a soft risk
budget, and an SOC target. Its output is `StrategyGuidance`, applied as SOFT priors
(`apply_guidance`) — never a hard command (principles #2, #4), or the design would just
swap the Truthful bottleneck for an LLM one. The guidance text is audit/UI metadata only
(principle #9), never execution logic.

The strategist runs off the critical tick path (principle #1): an async refresh updates a
cached `StrategyGuidance` that `decide()` reads non-blocking — the same pattern as the
ReflectiveAgent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from eflux.agents.reflective.prompt import _first_json_object
from eflux.agents.strategy.schema import StrategyAction, StrategyMode

log = logging.getLogger(__name__)

GUIDANCE_LESSON_MAX = 200
_VALID_MODES = {m.value for m in StrategyMode}


class GuidanceParseError(ValueError):
    """LLM response did not contain a usable guidance object."""


@dataclass(frozen=True)
class StrategyGuidance:
    """Slow strategic advice for the tactical policy (design note §5.1 JSON)."""

    preferred_modes: tuple[StrategyMode, ...] = ()
    avoid_modes: tuple[StrategyMode, ...] = ()
    risk_budget: float = 1.0  # [0,1] — scales order size / aggressiveness toward caution
    soc_target: float = 0.5  # [0,1] — desired battery state of charge
    execution_style: str = ""  # free text, audit/UI only
    lesson: str = ""  # persisted takeaway, audit/UI only

    def clamped(self) -> StrategyGuidance:
        return replace(
            self,
            risk_budget=max(0.0, min(1.0, float(self.risk_budget))),
            soc_target=max(0.0, min(1.0, float(self.soc_target))),
            execution_style=str(self.execution_style)[:200],
            lesson=str(self.lesson)[:GUIDANCE_LESSON_MAX],
        )


STRATEGIST_SYSTEM_PROMPT = """\
You are the slow strategist for a Virtual Power Plant trading in a continuous double
auction electricity market. A fast tactical policy chooses one trading PRIMITIVE each
tick; your job is to advise it over the current regime — never to place orders yourself.

Primitives the policy can use: noop, hold_energy, liquidate_surplus, cover_deficit,
passive_market_make, aggressive_taker, ladder_sell, ladder_buy, cancel_reprice,
battery_arbitrage.

Return ONLY a JSON object (no markdown, no commentary):
  {
    "preferred_modes": [<primitive names to favour>],
    "avoid_modes":     [<primitive names to discourage>],
    "risk_budget":     <float in [0,1]; 1 = full size, lower = more cautious>,
    "soc_target":      <float in [0,1]; desired battery state of charge>,
    "execution_style": "<short maker-vs-taker preference, <=120 chars>",
    "lesson":          "<one durable takeaway from the last window, <=200 chars>"
  }
Your advice is a SOFT prior — the tactical policy may override it. Stay within bounds."""


def build_strategist_system_prompt(persona_prompt: str | None = None) -> str:
    if not persona_prompt:
        return STRATEGIST_SYSTEM_PROMPT
    return f"{STRATEGIST_SYSTEM_PROMPT}\nPersona / standing brief:\n{persona_prompt.strip()}\n"


def build_strategist_user_message(
    *, recent_pnl: list[float], soc_frac: float, best_bid: float | None,
    best_ask: float | None, last_price: float | None, regime_note: str = "",
) -> str:
    payload = {
        "recent_pnl": [round(float(x), 4) for x in recent_pnl[-20:]],
        "soc_frac": round(float(soc_frac), 3),
        "best_bid": None if best_bid is None else round(float(best_bid), 4),
        "best_ask": None if best_ask is None else round(float(best_ask), 4),
        "last_price": None if last_price is None else round(float(last_price), 4),
        "regime_note": regime_note[:200],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _parse_modes(value) -> tuple[StrategyMode, ...]:
    if not isinstance(value, list):
        return ()
    out: list[StrategyMode] = []
    for item in value:
        name = str(item).strip().lower()
        if name in _VALID_MODES:
            out.append(StrategyMode(name))  # silently drop unknown names — soft input
    return tuple(out)


def parse_guidance(content: str) -> StrategyGuidance:
    """Extract the first JSON object and coerce it into clamped StrategyGuidance.
    Tolerant of prose/fences (same scanner as the reflective hint parser); raises
    GuidanceParseError when nothing usable is found so the caller logs a failure."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip("\n")
    data = _first_json_object(text)
    if data is None:
        raise GuidanceParseError(f"no JSON object in strategist response: {content[:160]!r}")
    try:
        return StrategyGuidance(
            preferred_modes=_parse_modes(data.get("preferred_modes")),
            avoid_modes=_parse_modes(data.get("avoid_modes")),
            risk_budget=float(data.get("risk_budget", 1.0)),
            soc_target=float(data.get("soc_target", 0.5)),
            execution_style=str(data.get("execution_style", "")),
            lesson=str(data.get("lesson", "")),
        ).clamped()
    except (TypeError, ValueError) as e:
        raise GuidanceParseError(f"guidance object has non-numeric fields: {data!r}") from e


def apply_guidance(action: StrategyAction, guidance: StrategyGuidance | None) -> StrategyAction:
    """Bias a chosen action by the guidance — softly. risk_budget scales size and
    aggressiveness toward caution; a discouraged primitive is shrunk (not forbidden);
    the SOC target is adopted. The primitive itself is left to the tactical policy, so
    guidance never becomes a hard command (principle #4)."""
    if guidance is None:
        return action
    qty = action.qty_fraction * guidance.risk_budget
    aggr = action.aggressiveness * guidance.risk_budget
    if action.mode in guidance.avoid_modes:
        qty *= 0.25  # discourage, don't veto
    return replace(action, qty_fraction=qty, aggressiveness=aggr, soc_target=guidance.soc_target)


@runtime_checkable
class Strategist(Protocol):
    def current_guidance(self) -> StrategyGuidance | None:
        ...


@dataclass
class StaticStrategist:
    """A fixed-guidance strategist — for tests, config-set guidance, or as the cache
    behind an async LLM refresh."""

    guidance: StrategyGuidance | None = None

    def current_guidance(self) -> StrategyGuidance | None:
        return self.guidance


@dataclass
class LLMStrategist:
    """Production strategist: an async refresh calls the LLM on a slow cadence and
    updates a cached StrategyGuidance that current_guidance() returns non-blocking, so
    the slow call never sits in the tick path (principle #1). A failed call keeps the
    prior guidance rather than blanking it."""

    client: object  # duck-typed: async chat(messages, *, temperature) -> str
    persona_prompt: str | None = None
    temperature: float = 0.3
    hard_timeout_sec: float = 180.0
    # Shared across all LLM-managed agents: at most one strategist call in flight.
    llm_gate: asyncio.Semaphore | None = None

    # Audit/health trail, shaped similarly to ReflectiveAgent.reflection_log so
    # existing API health code can reason about either implementation.
    reflection_log: deque = field(default_factory=lambda: deque(maxlen=50))
    ok_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
    last_ok_ts: datetime | None = None

    def __post_init__(self) -> None:
        self._guidance: StrategyGuidance | None = None

    def current_guidance(self) -> StrategyGuidance | None:
        return self._guidance

    async def arefresh(
        self, *, recent_pnl: list[float], soc_frac: float, best_bid: float | None,
        best_ask: float | None, last_price: float | None, regime_note: str = "",
    ) -> StrategyGuidance | None:
        if self.llm_gate is not None:
            if self.llm_gate.locked():
                self.skipped_count += 1
                return self._guidance
            async with self.llm_gate:
                return await self._refresh_once(
                    recent_pnl=recent_pnl,
                    soc_frac=soc_frac,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    last_price=last_price,
                    regime_note=regime_note,
                )
        return await self._refresh_once(
            recent_pnl=recent_pnl,
            soc_frac=soc_frac,
            best_bid=best_bid,
            best_ask=best_ask,
            last_price=last_price,
            regime_note=regime_note,
        )

    async def _refresh_once(
        self, *, recent_pnl: list[float], soc_frac: float, best_bid: float | None,
        best_ask: float | None, last_price: float | None, regime_note: str = "",
    ) -> StrategyGuidance | None:
        messages = [
            {"role": "system", "content": build_strategist_system_prompt(self.persona_prompt)},
            {"role": "user", "content": build_strategist_user_message(
                recent_pnl=recent_pnl, soc_frac=soc_frac, best_bid=best_bid,
                best_ask=best_ask, last_price=last_price, regime_note=regime_note,
            )},
        ]
        try:
            content = await asyncio.wait_for(
                self.client.chat(messages, temperature=self.temperature),
                timeout=self.hard_timeout_sec,
            )
            content = str(content)
            if not content.strip():
                raise RuntimeError("empty LLM response (completion budget exhausted?)")
            guidance = parse_guidance(content)
            self._guidance = guidance
            self.ok_count += 1
            self.last_ok_ts = datetime.now(UTC)
            self.reflection_log.append(self._entry(ok=True, guidance=guidance))
        except Exception as e:  # network error or unparseable response — keep prior
            self.fail_count += 1
            self.reflection_log.append(
                self._entry(ok=False, guidance=self._guidance, error=f"{type(e).__name__}: {e}")
            )
            log.warning("strategist refresh failed (%s); keeping prior guidance", type(e).__name__)
        return self._guidance

    def _entry(
        self,
        *,
        ok: bool,
        guidance: StrategyGuidance | None,
        error: str | None = None,
    ) -> dict:
        guidance = guidance or StrategyGuidance()
        ts = self.last_ok_ts if ok and self.last_ok_ts is not None else datetime.now(UTC)
        return {
            "ts": ts,
            "ok": ok,
            "preferred_modes": [m.value for m in guidance.preferred_modes],
            "avoid_modes": [m.value for m in guidance.avoid_modes],
            "risk_budget": guidance.risk_budget,
            "soc_target": guidance.soc_target,
            "execution_style": guidance.execution_style,
            # Keep rationale populated so older UI copy degrades gracefully.
            "rationale": guidance.execution_style or guidance.lesson,
            "lesson": guidance.lesson,
            "error": None if error is None else error[:200],
        }
