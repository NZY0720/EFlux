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
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from eflux.agents.strategy.schema import StrategyAction, StrategyMode

log = logging.getLogger(__name__)

GUIDANCE_LESSON_MAX = 200
_VALID_MODES = {m.value for m in StrategyMode}


def _first_json_object(text: str) -> dict | None:
    """Scan for the first decodable JSON object. raw_decode from each '{' handles
    prose around the object and trailing garbage; the old greedy `\\{.*\\}` regex
    spanned from the first to the *last* brace and broke on multi-object output.
    """
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            idx = text.find("{", idx + 1)
            continue
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)
    return None


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


def _clamp(x: float, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MetaControl:
    """The LLM's *constrained* meta-control over an online PPO learner (Part C).

    Distinct from `StrategyGuidance` (which biases execution): this steers *learning* —
    what the reward optimizes (weight multipliers), how the optimizer updates
    (lr / entropy / KL), and a pull toward preferred modes (mode_reg_coef). Every field is
    a safe no-op at its default and hard-clamped via `clamped()`, so a missing or malformed
    LLM response can only ever leave the learner at its baseline."""

    # Reward-weight multipliers — WHAT the learner optimizes (applied on the policy).
    w_imbalance_mult: float = 1.0  # [0.5, 2.0]
    w_soc_mult: float = 1.0        # [0.5, 2.0]
    w_degrade_mult: float = 1.0    # [0.5, 2.0]
    # Optimizer levers — HOW the learner updates.
    lr: float = 3e-4               # [1e-5, 1e-3]
    entropy_coef: float = 0.01     # [0, 0.05]
    kl_target: float = 0.02        # [0.005, 0.05]
    # Pull the policy's mode distribution toward preferred/avoid modes.
    mode_reg_coef: float = 0.0     # [0, 1.0]

    def clamped(self) -> MetaControl:
        return MetaControl(
            w_imbalance_mult=_clamp(self.w_imbalance_mult, 0.5, 2.0, 1.0),
            w_soc_mult=_clamp(self.w_soc_mult, 0.5, 2.0, 1.0),
            w_degrade_mult=_clamp(self.w_degrade_mult, 0.5, 2.0, 1.0),
            lr=_clamp(self.lr, 1e-5, 1e-3, 3e-4),
            entropy_coef=_clamp(self.entropy_coef, 0.0, 0.05, 0.01),
            kl_target=_clamp(self.kl_target, 0.005, 0.05, 0.02),
            mode_reg_coef=_clamp(self.mode_reg_coef, 0.0, 1.0, 0.0),
        )


def parse_meta_control(content: str) -> MetaControl:
    """Extract a `meta_control` object from the strategist response and clamp it. Tolerant
    by design: anything missing or unparseable yields a no-op `MetaControl` rather than
    raising — meta-control must never be able to break the (already-parsed) guidance path."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip("\n")
    data = _first_json_object(text)
    if not isinstance(data, dict):
        return MetaControl()
    block = data.get("meta_control")
    if not isinstance(block, dict):
        return MetaControl()
    d = MetaControl()
    return MetaControl(
        w_imbalance_mult=block.get("w_imbalance_mult", d.w_imbalance_mult),
        w_soc_mult=block.get("w_soc_mult", d.w_soc_mult),
        w_degrade_mult=block.get("w_degrade_mult", d.w_degrade_mult),
        lr=block.get("lr", d.lr),
        entropy_coef=block.get("entropy_coef", d.entropy_coef),
        kl_target=block.get("kl_target", d.kl_target),
        mode_reg_coef=block.get("mode_reg_coef", d.mode_reg_coef),
    ).clamped()


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
    "lesson":          "<one durable takeaway from the last window, <=200 chars>",
    "meta_control": {
      "w_imbalance_mult": <float in [0.5,2]; >1 punishes leaving energy unserved harder>,
      "w_soc_mult":       <float in [0.5,2]; >1 protects the battery SOC band harder>,
      "w_degrade_mult":   <float in [0.5,2]; >1 discourages battery cycling harder>,
      "lr":               <float in [1e-5,1e-3]; learning rate of the policy>,
      "entropy_coef":     <float in [0,0.05]; higher = explore more>,
      "kl_target":        <float in [0.005,0.05]; smaller = more cautious updates>,
      "mode_reg_coef":    <float in [0,1]; >0 pulls the learner toward preferred_modes>
    }
  }
If the policy is learning well, omit "meta_control" or leave it at the defaults. Your
advice and meta-control are SOFT, clamped priors — the tactical policy may override the
advice, and out-of-range knobs are clipped. Stay within bounds."""

REALPRICE_STRATEGIST_SYSTEM_PROMPT = """\
You are the slow strategist for a Virtual Power Plant trading in a real-time grid
price market. There is no peer order book: every accepted order settles against the
CAISO grid quote, and the VPP cannot move the price. A fast tactical policy chooses
one trading PRIMITIVE each tick; your job is to advise timing, SOC posture, risk, and
learning meta-control over the current grid-price regime — never to place orders
yourself.

Primitives the policy can use: noop, hold_energy, liquidate_surplus, cover_deficit,
aggressive_taker, battery_arbitrage. Book-specific primitives may exist in the shared
action vocabulary, but they are not useful here and should not be preferred.

Return ONLY a JSON object (no markdown, no commentary):
  {
    "preferred_modes": [<primitive names to favour>],
    "avoid_modes":     [<primitive names to discourage>],
    "risk_budget":     <float in [0,1]; 1 = full size, lower = more cautious>,
    "soc_target":      <float in [0,1]; desired battery state of charge>,
    "execution_style": "<short grid-price timing preference, <=120 chars>",
    "lesson":          "<one durable takeaway from the last window, <=200 chars>",
    "meta_control": {
      "w_imbalance_mult": <float in [0.5,2]; >1 punishes leaving energy unserved harder>,
      "w_soc_mult":       <float in [0.5,2]; >1 protects the battery SOC band harder>,
      "w_degrade_mult":   <float in [0.5,2]; >1 discourages battery cycling harder>,
      "lr":               <float in [1e-5,1e-3]; learning rate of the policy>,
      "entropy_coef":     <float in [0,0.05]; higher = explore more>,
      "kl_target":        <float in [0.005,0.05]; smaller = more cautious updates>,
      "mode_reg_coef":    <float in [0,1]; >0 pulls the learner toward preferred_modes>
    }
  }
If the policy is learning well, omit "meta_control" or leave it at the defaults. Treat
null best_bid/best_ask as normal in this market. Your advice and meta-control are SOFT,
clamped priors — the tactical policy may override the advice, and out-of-range knobs
are clipped. Stay within bounds."""

_REALPRICE_DISALLOWED_PREFERRED = {
    StrategyMode.PASSIVE_MARKET_MAKE,
    StrategyMode.LADDER_SELL,
    StrategyMode.LADDER_BUY,
    StrategyMode.CANCEL_REPRICE,
}


def build_strategist_system_prompt(
    persona_prompt: str | None = None, *, market_mode: str = "p2p"
) -> str:
    base = REALPRICE_STRATEGIST_SYSTEM_PROMPT if market_mode == "realprice" else STRATEGIST_SYSTEM_PROMPT
    if not persona_prompt:
        return base
    return f"{base}\nPersona / standing brief:\n{persona_prompt.strip()}\n"


def build_strategist_user_message(
    *, recent_pnl: list[float], soc_frac: float, best_bid: float | None,
    best_ask: float | None, last_price: float | None, regime_note: str = "",
    market_mode: str = "p2p", grid_raw_lmp: float | None = None,
    grid_import_price: float | None = None, grid_export_price: float | None = None,
    grid_status: str | None = None,
) -> str:
    payload = {
        "market_mode": market_mode,
        "recent_pnl": [round(float(x), 4) for x in recent_pnl[-20:]],
        "soc_frac": round(float(soc_frac), 3),
        "best_bid": None if best_bid is None else round(float(best_bid), 4),
        "best_ask": None if best_ask is None else round(float(best_ask), 4),
        "last_price": None if last_price is None else round(float(last_price), 4),
        "regime_note": regime_note[:200],
    }
    if market_mode == "realprice":
        payload.update(
            {
                "grid_raw_lmp": None if grid_raw_lmp is None else round(float(grid_raw_lmp), 4),
                "grid_import_price": None if grid_import_price is None else round(float(grid_import_price), 4),
                "grid_export_price": None if grid_export_price is None else round(float(grid_export_price), 4),
                "grid_status": grid_status,
            }
        )
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


def _sanitize_guidance_for_market(guidance: StrategyGuidance, market_mode: str) -> StrategyGuidance:
    if market_mode != "realprice":
        return guidance
    preferred = tuple(m for m in guidance.preferred_modes if m not in _REALPRICE_DISALLOWED_PREFERRED)
    return replace(guidance, preferred_modes=preferred)


def parse_guidance(content: str, *, market_mode: str = "p2p") -> StrategyGuidance:
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
        guidance = StrategyGuidance(
            preferred_modes=_parse_modes(data.get("preferred_modes")),
            avoid_modes=_parse_modes(data.get("avoid_modes")),
            risk_budget=float(data.get("risk_budget", 1.0)),
            soc_target=float(data.get("soc_target", 0.5)),
            execution_style=str(data.get("execution_style", "")),
            lesson=str(data.get("lesson", "")),
        ).clamped()
        return _sanitize_guidance_for_market(guidance, market_mode)
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
    meta: MetaControl | None = None

    def current_guidance(self) -> StrategyGuidance | None:
        return self.guidance

    def current_meta(self) -> MetaControl | None:
        return self.meta


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
    # Backtest-only strict mode: surface LLM failures to the caller instead of
    # degrading to cached guidance. Live agents keep the default tolerant behavior.
    raise_errors: bool = False

    # Audit/health trail, shaped similarly to ReflectiveAgent.reflection_log so
    # existing API health code can reason about either implementation.
    reflection_log: deque = field(default_factory=lambda: deque(maxlen=50))
    ok_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
    last_ok_ts: datetime | None = None

    def __post_init__(self) -> None:
        self._guidance: StrategyGuidance | None = None
        self._meta: MetaControl | None = None

    def current_guidance(self) -> StrategyGuidance | None:
        return self._guidance

    def current_meta(self) -> MetaControl | None:
        """The latest constrained meta-control (None until a refresh succeeds). Read
        non-blocking off the tick path, exactly like current_guidance (principle #1)."""
        return self._meta

    async def arefresh(
        self, *, recent_pnl: list[float], soc_frac: float, best_bid: float | None,
        best_ask: float | None, last_price: float | None, regime_note: str = "",
        market_mode: str = "p2p", grid_raw_lmp: float | None = None,
        grid_import_price: float | None = None, grid_export_price: float | None = None,
        grid_status: str | None = None,
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
                    market_mode=market_mode,
                    grid_raw_lmp=grid_raw_lmp,
                    grid_import_price=grid_import_price,
                    grid_export_price=grid_export_price,
                    grid_status=grid_status,
                )
        return await self._refresh_once(
            recent_pnl=recent_pnl,
            soc_frac=soc_frac,
            best_bid=best_bid,
            best_ask=best_ask,
            last_price=last_price,
            regime_note=regime_note,
            market_mode=market_mode,
            grid_raw_lmp=grid_raw_lmp,
            grid_import_price=grid_import_price,
            grid_export_price=grid_export_price,
            grid_status=grid_status,
        )

    async def _refresh_once(
        self, *, recent_pnl: list[float], soc_frac: float, best_bid: float | None,
        best_ask: float | None, last_price: float | None, regime_note: str = "",
        market_mode: str = "p2p", grid_raw_lmp: float | None = None,
        grid_import_price: float | None = None, grid_export_price: float | None = None,
        grid_status: str | None = None,
    ) -> StrategyGuidance | None:
        messages = [
            {
                "role": "system",
                "content": build_strategist_system_prompt(
                    self.persona_prompt, market_mode=market_mode
                ),
            },
            {"role": "user", "content": build_strategist_user_message(
                recent_pnl=recent_pnl, soc_frac=soc_frac, best_bid=best_bid,
                best_ask=best_ask, last_price=last_price, regime_note=regime_note,
                market_mode=market_mode, grid_raw_lmp=grid_raw_lmp,
                grid_import_price=grid_import_price, grid_export_price=grid_export_price,
                grid_status=grid_status,
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
            guidance = parse_guidance(content, market_mode=market_mode)
            meta = parse_meta_control(content)  # tolerant — never raises
            self._guidance = guidance
            self._meta = meta
            self.ok_count += 1
            self.last_ok_ts = datetime.now(UTC)
            self.reflection_log.append(self._entry(ok=True, guidance=guidance, meta=meta))
        except Exception as e:  # network error or unparseable response — keep prior
            self.fail_count += 1
            self.reflection_log.append(
                self._entry(
                    ok=False,
                    guidance=self._guidance,
                    meta=self._meta,
                    error=f"{type(e).__name__}: {e}",
                )
            )
            if self.raise_errors:
                log.error("strategist refresh failed (%s); raising in strict mode", type(e).__name__)
                raise
            log.warning("strategist refresh failed (%s); keeping prior guidance", type(e).__name__)
        return self._guidance

    def _entry(
        self,
        *,
        ok: bool,
        guidance: StrategyGuidance | None,
        meta: MetaControl | None = None,
        error: str | None = None,
    ) -> dict:
        guidance = guidance or StrategyGuidance()
        meta_dict = None if meta is None else {k: float(v) for k, v in asdict(meta).items()}
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
            "meta_control": meta_dict,
            "error": None if error is None else error[:200],
        }
