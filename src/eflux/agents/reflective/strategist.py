"""Structured LLM guidance — the slow strategist layer (design note §5.1).

The LLM operates over a longer horizon than the tactical policy: it reads the regime,
reviews the last window, and recommends/discourages strategy primitives, a risk budget,
an optional binding mode pin, and an SOC target. Its output is `StrategyGuidance`,
applied as clamped execution guidance (`apply_guidance`): binding levers can halt,
veto modes, or force passive execution; the remaining fields bias execution. The guidance
text is audit/UI metadata only (principle #9), never execution logic.

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
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from eflux.agents.strategy.schema import StrategyAction, StrategyMode

log = logging.getLogger(__name__)

GUIDANCE_LESSON_MAX = 200
RISK_BUDGET_MAX = 1.5
PRICE_BIAS_BPS_MAX = 200.0
_VALID_MODES = {m.value for m in StrategyMode}
_FORECAST_TARGETS = ("price_real", "price_p2p", "ghi")
_FORECAST_HORIZONS = ("5m", "1h", "12h")


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
    mode_pin: StrategyMode | None = None  # binding primitive override for the next window
    halt: bool = False  # binding no-new-orders; never implies physical battery charging
    passive_only: bool = False  # binding maker-only execution: never cross
    risk_budget: float = 1.0  # [0,1.5] — scales order size / aggressiveness
    price_bias_bps: float = 0.0  # [-200,200] — shifts quotes off fair value
    soc_target: float = 0.5  # [0,1] — desired battery state of charge
    execution_style: str = ""  # free text, audit/UI only
    lesson: str = ""  # persisted takeaway, audit/UI only

    def clamped(self) -> StrategyGuidance:
        return replace(
            self,
            risk_budget=max(0.0, min(RISK_BUDGET_MAX, float(self.risk_budget))),
            price_bias_bps=max(
                -PRICE_BIAS_BPS_MAX,
                min(PRICE_BIAS_BPS_MAX, float(self.price_bias_bps)),
            ),
            soc_target=max(0.0, min(1.0, float(self.soc_target))),
            execution_style=str(self.execution_style)[:200],
            lesson=str(self.lesson)[:GUIDANCE_LESSON_MAX],
        )


def _clamp(x: float, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return default


def _rounded_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def compact_forecast_for_strategist(forecast: object | None) -> dict | None:
    """Reduce a ForecastBundle-like object or forecast dict to strategist input values."""
    if forecast is None:
        return None
    out: dict[str, dict[str, float | None]] = {}
    for target in _FORECAST_TARGETS:
        series = (
            forecast.get(target) if isinstance(forecast, dict) else getattr(forecast, target, None)
        )
        if series is None:
            continue
        horizons: dict[str, float | None] = {}
        for horizon in _FORECAST_HORIZONS:
            point = None
            if isinstance(series, dict):
                point = series.get(horizon)
                if isinstance(point, dict):
                    point = point.get("value")
            else:
                try:
                    point = series.by_horizon(horizon).value
                except (AttributeError, KeyError, TypeError, ValueError):
                    point = None
            horizons[horizon] = _rounded_or_none(point)
        out[target] = horizons
    return out or None


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
    w_soc_mult: float = 1.0  # [0.5, 2.0]
    w_degrade_mult: float = 1.0  # [0.5, 2.0]
    # Optimizer levers — HOW the learner updates.
    lr: float = 3e-4  # [1e-5, 1e-3]
    entropy_coef: float = 0.01  # [0, 0.05]
    kl_target: float = 0.02  # [0.005, 0.05]
    # Pull the policy's mode distribution toward preferred/avoid modes.
    mode_reg_coef: float = 0.0  # [0, 1.0]

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
    "mode_pin":        <primitive name or null; BINDING next-window override; use sparingly>,
    "halt":         <bool; BINDING: place no new orders. This does NOT itself charge the battery>,
    "passive_only": <bool; BINDING: maker-only, never cross the spread. Use when liquidity is thin>,
    "risk_budget":     <float in [0,1.5]; 1 = full size, >1 = press harder, <1 = cautious>,
    "price_bias_bps":  <float in [-200,200]; shifts quotes off fair value; positive = quote higher>,
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
Read `regime_note` in the input and act on extremes with the BINDING levers, not just soft advice:
- Heavy oversupply / price collapsed near zero: do NOT keep dumping surplus. Prefer an explicit battery-charging primitive when SOC/power headroom exists. Use "halt" only to stop new orders when the physical position remains safe; it never stores energy by itself. Add "liquidate_surplus" to "avoid_modes", lower "risk_budget", and set "meta_control".w_soc_mult below 1 so charging is not penalized.
- Thin/illiquid book: set "passive_only": true (p2p: prefer "passive_market_make").
- Scarcity (bids elevated, few/no asks): press with "risk_budget" > 1 and prefer "aggressive_taker" / "cover_deficit".

A `forecast` block may be present with 5m/1h/12h grid price, peer price, and irradiance.
Act on it: charge or hold_energy ahead of forecast price spikes, be patient selling into a
rising forecast, and press or de-risk ahead of a forecast collapse.

A `performance_window` may contain recent real-USD PnL deltas, fills, rejection deltas,
realized absolute imbalance, residual contract exposure, SOC, and open-order counts.
Use changes across the window—not just the latest cumulative PnL—to diagnose whether the
previous guidance improved execution. Avoid increasing risk after worsening imbalance or rejects.

If the policy is learning well, omit "meta_control" or leave it at the defaults.
mode_pin, halt, passive_only, and avoid_modes are BINDING; the other fields are soft, clamped priors — the tactical policy may override those, and out-of-range knobs are clipped. Stay within bounds."""

REALPRICE_STRATEGIST_SYSTEM_PROMPT = """\
You are the slow strategist for a Virtual Power Plant trading in a real-time grid
price market. There is no peer order book: every accepted order settles against the
CAISO grid quote, and the VPP cannot move the price. A fast tactical policy chooses
one trading PRIMITIVE each tick; your job is to advise timing, SOC posture, risk, and
learning meta-control over the current grid-price regime — never to place orders
yourself.

Primitives the policy can use: noop, hold_energy, liquidate_surplus, cover_deficit,
aggressive_taker, battery_arbitrage, grid_charge_on_dip, grid_discharge_on_peak,
wait_for_better. That list is exhaustive here: order-book quoting primitives from the
shared action vocabulary are invalid in this market and are removed before execution,
so never prefer or pin anything outside the list above.

Grid timing primitives:
- grid_charge_on_dip: buy from the grid to charge the battery when current import price
  is materially below the forecast reference.
- grid_discharge_on_peak: sell battery energy to the grid when current export price is
  materially above the forecast reference.
- wait_for_better: place no orders when the battery can bridge a near-term imbalance
  and the forecast says a better grid price is imminent.

Return ONLY a JSON object (no markdown, no commentary):
  {
    "preferred_modes": [<primitive names to favour>],
    "avoid_modes":     [<primitive names to discourage>],
    "mode_pin":        <primitive name or null; BINDING next-window override; use sparingly>,
    "halt":         <bool; BINDING: place no new orders. This does NOT itself charge the battery>,
    "passive_only": <bool; no effect in this market (there is no book to quote into); leave false>,
    "risk_budget":     <float in [0,1.5]; 1 = full size, >1 = press harder, <1 = cautious>,
    "price_bias_bps":  <float in [-200,200]; shifts quotes off fair value; positive = quote higher>,
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
Read `regime_note` in the input and act on extremes with the BINDING levers, not just soft advice:
- Grid price collapsed near zero / heavy solar oversupply: do NOT keep dumping surplus. Prefer "grid_charge_on_dip" only when SOC/power headroom exists. Use "halt" only to stop new orders when the physical position remains safe; it never stores energy by itself. Add "liquidate_surplus" to "avoid_modes", lower "risk_budget", and set "meta_control".w_soc_mult below 1 so charging is not penalized.
- Grid price spike / scarcity hours: press with "risk_budget" > 1, prefer "grid_discharge_on_peak" / "cover_deficit", and avoid charging into the spike.
- Whipsawing grid price: favour "wait_for_better" and battery buffering over chasing every move.

A `forecast` block may be present with 5m/1h/12h grid price, peer price, and irradiance.
Act on it: charge or hold_energy ahead of forecast price spikes, be patient selling into a
rising forecast, and press or de-risk ahead of a forecast collapse.

A `performance_window` may contain recent real-USD PnL deltas, fills, rejection deltas,
realized absolute imbalance, residual contract exposure, SOC, and open-order counts.
Use changes across the window—not just the latest cumulative PnL—to diagnose whether the
previous guidance improved execution. Avoid increasing risk after worsening imbalance or rejects.

If the policy is learning well, omit "meta_control" or leave it at the defaults. The market
input's best_bid/best_ask are null here (no order book) — that is normal, not an error.
mode_pin, halt, and avoid_modes are BINDING; the other fields are soft, clamped priors — the tactical policy may override those, and out-of-range knobs are clipped. Stay within bounds."""

_GRID_NATIVE_MODES = {
    StrategyMode.GRID_CHARGE_ON_DIP,
    StrategyMode.GRID_DISCHARGE_ON_PEAK,
    StrategyMode.WAIT_FOR_BETTER,
}

_REALPRICE_DISALLOWED_PREFERRED = {
    StrategyMode.PASSIVE_MARKET_MAKE,
    StrategyMode.LADDER_SELL,
    StrategyMode.LADDER_BUY,
    StrategyMode.CANCEL_REPRICE,
}

_REALPRICE_ALLOWED_MODES = {
    StrategyMode.NOOP,
    StrategyMode.HOLD_ENERGY,
    StrategyMode.LIQUIDATE_SURPLUS,
    StrategyMode.COVER_DEFICIT,
    StrategyMode.AGGRESSIVE_TAKER,
    StrategyMode.BATTERY_ARBITRAGE,
    StrategyMode.GRID_CHARGE_ON_DIP,
    StrategyMode.GRID_DISCHARGE_ON_PEAK,
    StrategyMode.WAIT_FOR_BETTER,
}
_P2P_ALLOWED_MODES = set(StrategyMode) - _GRID_NATIVE_MODES


def allowed_modes_for_market(market_mode: str) -> set[StrategyMode]:
    return set(_REALPRICE_ALLOWED_MODES if market_mode == "realprice" else _P2P_ALLOWED_MODES)


def build_strategist_system_prompt(
    persona_prompt: str | None = None, *, market_mode: str = "p2p"
) -> str:
    base = (
        REALPRICE_STRATEGIST_SYSTEM_PROMPT
        if market_mode == "realprice"
        else STRATEGIST_SYSTEM_PROMPT
    )
    base = (
        f"{base}\nThe input includes an `endowment` block (your VPP's own battery/PV/load/gas "
        "sizes) and a `character` block (archetype, risk_appetite, SOC band). Tailor guidance to "
        "them: a large-battery arbitrageur presses spreads and swings SOC wide; a load-heavy "
        "consumer minimizes cost and keeps charge in reserve; a producer time-shifts and sells its surplus."
    )
    if not persona_prompt:
        return base
    return f"{base}\nPersona / standing brief:\n{persona_prompt.strip()}\n"


def build_strategist_user_message(
    *,
    recent_pnl: list[float],
    soc_frac: float,
    best_bid: float | None,
    best_ask: float | None,
    last_price: float | None,
    regime_note: str = "",
    market_mode: str = "p2p",
    grid_raw_lmp: float | None = None,
    grid_import_price: float | None = None,
    grid_export_price: float | None = None,
    grid_status: str | None = None,
    forecast: dict | None = None,
    endowment: dict | None = None,
    character: dict | None = None,
    performance_window: list[dict] | None = None,
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
                "grid_import_price": None
                if grid_import_price is None
                else round(float(grid_import_price), 4),
                "grid_export_price": None
                if grid_export_price is None
                else round(float(grid_export_price), 4),
                "grid_status": grid_status,
            }
        )
    compact_forecast = compact_forecast_for_strategist(forecast)
    if compact_forecast is not None:
        payload["forecast"] = compact_forecast
    if endowment:
        payload["endowment"] = endowment
    if character:
        payload["character"] = character
    if performance_window:
        # The producer already rounds and bounds the schema; slicing here is a
        # final prompt-size guard for third-party strategist callers.
        payload["performance_window"] = performance_window[-20:]
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


def _parse_mode_pin(value) -> StrategyMode | None:
    if value is None:
        return None
    name = str(value).strip().lower()
    return StrategyMode(name) if name in _VALID_MODES else None


def _parse_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y", "on"}:
            return True
        if v in {"false", "0", "no", "n", "off"}:
            return False
    return default


def modes_from_names(names: list[str] | tuple[str, ...]) -> tuple[StrategyMode, ...]:
    """Public soft parser for externally supplied mode names (Tier A3 guidance
    ingestion): unknown names are dropped, never an error — same tolerance the
    LLM output path gets."""
    return _parse_modes(list(names))


def _sanitize_guidance_for_market(guidance: StrategyGuidance, market_mode: str) -> StrategyGuidance:
    allowed = allowed_modes_for_market(market_mode)
    preferred = tuple(m for m in guidance.preferred_modes if m in allowed)
    mode_pin = guidance.mode_pin if guidance.mode_pin in allowed else None
    return replace(guidance, preferred_modes=preferred, mode_pin=mode_pin)


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
        d = StrategyGuidance()
        guidance = StrategyGuidance(
            preferred_modes=_parse_modes(data.get("preferred_modes")),
            avoid_modes=_parse_modes(data.get("avoid_modes")),
            mode_pin=_parse_mode_pin(data.get("mode_pin")),
            halt=_parse_bool(data.get("halt"), d.halt),
            passive_only=_parse_bool(data.get("passive_only"), d.passive_only),
            risk_budget=_clamp(
                data.get("risk_budget", d.risk_budget), 0.0, RISK_BUDGET_MAX, d.risk_budget
            ),
            price_bias_bps=_clamp(
                data.get("price_bias_bps", d.price_bias_bps),
                -PRICE_BIAS_BPS_MAX,
                PRICE_BIAS_BPS_MAX,
                d.price_bias_bps,
            ),
            soc_target=_clamp(data.get("soc_target", d.soc_target), 0.0, 1.0, d.soc_target),
            execution_style=str(data.get("execution_style", "")),
            lesson=str(data.get("lesson", "")),
        ).clamped()
        return _sanitize_guidance_for_market(guidance, market_mode)
    except (TypeError, ValueError) as e:
        raise GuidanceParseError(f"guidance object has non-numeric fields: {data!r}") from e


def external_guidance_from_dict(
    data: dict, *, market_mode: str = "p2p"
) -> tuple[StrategyGuidance, MetaControl | None]:
    """Coerce an externally supplied guidance payload (Tier A3 API body, or the copy
    persisted in managed_config) into clamped, market-sanitized objects.

    As soft as the LLM path: unknown mode names are dropped, numbers are clamped,
    and a malformed meta_control degrades to None rather than raising. Server-side
    clamping is authoritative — callers echo the result, never the raw input.
    """
    d = StrategyGuidance()
    guidance = StrategyGuidance(
        preferred_modes=modes_from_names(list(data.get("preferred_modes") or [])),
        avoid_modes=modes_from_names(list(data.get("avoid_modes") or [])),
        mode_pin=_parse_mode_pin(data.get("mode_pin")),
        halt=_parse_bool(data.get("halt"), d.halt),
        passive_only=_parse_bool(data.get("passive_only"), d.passive_only),
        risk_budget=_clamp(
            data.get("risk_budget", d.risk_budget), 0.0, RISK_BUDGET_MAX, d.risk_budget
        ),
        price_bias_bps=_clamp(
            data.get("price_bias_bps", d.price_bias_bps),
            -PRICE_BIAS_BPS_MAX,
            PRICE_BIAS_BPS_MAX,
            d.price_bias_bps,
        ),
        soc_target=_clamp(data.get("soc_target", d.soc_target), 0.0, 1.0, d.soc_target),
        execution_style=str(data.get("execution_style") or ""),
        lesson=str(data.get("lesson") or ""),
    ).clamped()
    guidance = _sanitize_guidance_for_market(guidance, market_mode)

    meta: MetaControl | None = None
    block = data.get("meta_control")
    if isinstance(block, dict):
        d = MetaControl()
        try:
            meta = MetaControl(
                **{
                    f.name: float(block.get(f.name, getattr(d, f.name)))
                    for f in dataclass_fields(MetaControl)
                }
            ).clamped()
        except (TypeError, ValueError):
            meta = None  # tolerant, like parse_meta_control
    return guidance, meta


def apply_guidance(action: StrategyAction, guidance: StrategyGuidance | None) -> StrategyAction:
    """Apply clamped strategic guidance to a chosen action.

    Binding precedence is mode_pin > halt > avoid_modes. risk_budget scales
    size/aggressiveness, passive_only forces maker-only aggressiveness, price_bias_bps
    shifts the quote, and soc_target is adopted.
    """
    if guidance is None:
        return action
    mode = guidance.mode_pin if guidance.mode_pin is not None else action.mode
    if guidance.mode_pin is None:
        if guidance.halt:
            mode = StrategyMode.HOLD_ENERGY
        elif mode in guidance.avoid_modes:
            mode = StrategyMode.HOLD_ENERGY
    qty = action.qty_fraction * guidance.risk_budget
    aggr = min(1.0, action.aggressiveness * guidance.risk_budget)
    if guidance.passive_only:
        aggr = 0.0
    bps = action.price_offset_bps + guidance.price_bias_bps
    return replace(
        action,
        mode=mode,
        qty_fraction=qty,
        aggressiveness=aggr,
        price_offset_bps=bps,
        soc_target=guidance.soc_target,
    )


@runtime_checkable
class Strategist(Protocol):
    def current_guidance(self) -> StrategyGuidance | None: ...


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
class ExternalStrategist:
    """Externally steered strategist — Tier A3 of docs/EXTERNAL_PARTICIPATION.md.

    The user's own LLM (or any local process) posts StrategyGuidance over the API;
    this object holds the latest clamped guidance and mimics LLMStrategist's audit
    surface (reflection_log entries with the exact _entry key set, ok/fail counters,
    last_ok_ts) so the performance panels, /market/reflections, and health badges
    keep working unchanged.

    Deliberately has NO ``arefresh`` attribute: HybridPolicyAgent._maybe_refresh_guidance
    no-ops without it, so the platform LLM is never called while external guidance is
    active — the documented zero-platform-cost incentive of running your own model.
    """

    # The platform strategist this one displaced; restored on release
    # (DELETE /vpps/managed/{id}/guidance).
    prior: object | None = None
    # Copied from prior so model attribution (UI badges, chatroom eligibility) survives.
    client: object | None = None
    # Passed in from prior at swap time so the audit timeline stays continuous.
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
        return self._meta

    def set_guidance(self, guidance: StrategyGuidance, meta: MetaControl | None = None) -> dict:
        """Adopt externally supplied (already clamped/sanitized) guidance and record
        the audit entry. Returns the entry so the API can echo what was applied."""
        self._guidance = guidance
        self._meta = meta
        self.ok_count += 1
        self.last_ok_ts = datetime.now(UTC)
        meta_dict = None if meta is None else {k: float(v) for k, v in asdict(meta).items()}
        entry = {
            "ts": self.last_ok_ts,
            "ok": True,
            "preferred_modes": [m.value for m in guidance.preferred_modes],
            "avoid_modes": [m.value for m in guidance.avoid_modes],
            "mode_pin": None if guidance.mode_pin is None else guidance.mode_pin.value,
            "halt": guidance.halt,
            "passive_only": guidance.passive_only,
            "risk_budget": guidance.risk_budget,
            "price_bias_bps": guidance.price_bias_bps,
            "soc_target": guidance.soc_target,
            "execution_style": guidance.execution_style,
            # Same fallback as LLMStrategist._entry so older UI copy degrades gracefully;
            # tagged so readers can tell external steering from platform reflections.
            "rationale": (guidance.execution_style or "external guidance"),
            "lesson": guidance.lesson,
            "meta_control": meta_dict,
            "error": None,
        }
        self.reflection_log.append(entry)
        return entry


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
        self,
        *,
        recent_pnl: list[float],
        soc_frac: float,
        best_bid: float | None,
        best_ask: float | None,
        last_price: float | None,
        regime_note: str = "",
        market_mode: str = "p2p",
        grid_raw_lmp: float | None = None,
        grid_import_price: float | None = None,
        grid_export_price: float | None = None,
        grid_status: str | None = None,
        forecast: dict | None = None,
        endowment: dict | None = None,
        character: dict | None = None,
        performance_window: list[dict] | None = None,
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
                    forecast=forecast,
                    endowment=endowment,
                    character=character,
                    performance_window=performance_window,
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
            forecast=forecast,
            endowment=endowment,
            character=character,
            performance_window=performance_window,
        )

    async def _refresh_once(
        self,
        *,
        recent_pnl: list[float],
        soc_frac: float,
        best_bid: float | None,
        best_ask: float | None,
        last_price: float | None,
        regime_note: str = "",
        market_mode: str = "p2p",
        grid_raw_lmp: float | None = None,
        grid_import_price: float | None = None,
        grid_export_price: float | None = None,
        grid_status: str | None = None,
        forecast: dict | None = None,
        endowment: dict | None = None,
        character: dict | None = None,
        performance_window: list[dict] | None = None,
    ) -> StrategyGuidance | None:
        messages = [
            {
                "role": "system",
                "content": build_strategist_system_prompt(
                    self.persona_prompt, market_mode=market_mode
                ),
            },
            {
                "role": "user",
                "content": build_strategist_user_message(
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
                    forecast=forecast,
                    endowment=endowment,
                    character=character,
                    performance_window=performance_window,
                ),
            },
        ]
        try:
            content = await asyncio.wait_for(
                self.client.chat(messages, temperature=self.temperature),
                timeout=self.hard_timeout_sec,
            )
            content = str(content)
            if not content.strip():
                raise RuntimeError("empty LLM response")
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
                log.error(
                    "strategist refresh failed (%s); raising in strict mode", type(e).__name__
                )
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
            "mode_pin": None if guidance.mode_pin is None else guidance.mode_pin.value,
            "halt": guidance.halt,
            "passive_only": guidance.passive_only,
            "risk_budget": guidance.risk_budget,
            "price_bias_bps": guidance.price_bias_bps,
            "soc_target": guidance.soc_target,
            "execution_style": guidance.execution_style,
            # Keep rationale populated so older UI copy degrades gracefully.
            "rationale": guidance.execution_style or "strategy guidance",
            "lesson": guidance.lesson,
            "meta_control": meta_dict,
            "llm_usage": getattr(self.client, "usage", None),
            "error": None if error is None else error[:200],
        }
