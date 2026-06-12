"""Prompt templates + response parsing for the reflective agent.

The LLM is asked to play strategy advisor over a sliding window of recent activity.
It returns a small JSON with bounded adjustments — never raw orders — so a bad
response can't blow up the matching engine.

Context size is bounded by construction: every section has a hard slice constant,
so the prompt cannot grow with runtime.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Hard caps on prompt sections — the token budget is bounded by construction.
MAX_OUTCOMES = 5  # past hint→outcome records (learning from self)
MAX_PEER = 3  # other LLM agents' latest views (learning from others)
MAX_MKT_TRADES = 8  # market-wide fills (learning from the market)
LESSON_MAX = 160


class HintParseError(ValueError):
    """LLM response did not contain a usable hints object."""

SYSTEM_PROMPT = """\
You are a trading-strategy advisor for a Virtual Power Plant (VPP) participating in a
continuous double auction electricity market. Your job is to nudge a baseline
truthful-pricing strategy based on recent market and self performance.

The market settles in real time with a CDA limit order book. Sellers post asks,
buyers post bids; orders cross when bid >= ask, trading at the resting order's price.

You will receive:
  - the last N tick PnL deltas (your own)
  - the last K trades that touched your VPP
  - your current SOC (state of charge, 0-1)
  - the current best bid/ask and last trade price
  - past_hint_outcomes: your own previous adjustments and the PnL/trades observed
    over the window that followed each one — learn from what actually worked
  - recent_market_trades: who traded with whom at what price (the whole market)
  - peer_llm_views: what the other LLM-steered VPPs decided most recently —
    they are your competitors; imitate what works, exploit what doesn't

Return ONLY a JSON object with these keys (no commentary, no markdown fences):
  {
    "price_adjust": <float in [-0.20, 0.20]>,    // multiplicative offset, e.g. 0.05 = bid 5% higher / ask 5% lower
    "qty_scale":    <float in [0.5, 1.5]>,       // multiplicative scaling of base order qty
    "rationale":    "<short string, <=120 chars>",
    "lesson":       "<one generalizable takeaway from your past outcomes, <=160 chars>"
  }

The lesson is persisted and fed back to you in future sessions — make it a durable
rule of thumb, not a restatement of the current numbers.
Stay within the numerical bounds. If unsure, return zeros / ones.
"""


def build_system_prompt(persona_prompt: str | None = None) -> str:
    """Base advisor brief, optionally specialized with the agent's persona."""
    if not persona_prompt:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\nYour persona and standing strategy brief:\n{persona_prompt.strip()}\n"


@dataclass
class ReflectionHints:
    price_adjust: float = 0.0
    qty_scale: float = 1.0
    rationale: str = ""
    lesson: str = ""

    def clamped(self) -> ReflectionHints:
        return ReflectionHints(
            price_adjust=max(-0.20, min(0.20, float(self.price_adjust))),
            qty_scale=max(0.5, min(1.5, float(self.qty_scale))),
            rationale=str(self.rationale)[:120],
            lesson=str(self.lesson)[:LESSON_MAX],
        )


def _compact_outcome(record: dict) -> dict:
    """Memory record → the compact form the prompt carries."""
    hints = record.get("hints") or {}
    window = record.get("window") or {}
    return {
        "pa": hints.get("price_adjust"),
        "qs": hints.get("qty_scale"),
        "pnl": window.get("pnl"),
        "trades": window.get("trades"),
        "lesson": str(record.get("lesson") or "")[:LESSON_MAX],
    }


def _compact_own_trade(trade: dict) -> dict:
    """Runner trade record → side/price/qty (+id), dropping bulky bookkeeping."""
    return {
        "trade_id": trade.get("trade_id"),
        "side": trade.get("side"),
        "price": trade.get("price"),
        "qty": trade.get("qty"),
    }


def build_user_message(
    *,
    recent_pnl: list[float],
    recent_trades: list[dict],
    soc_frac: float,
    best_bid: float | None,
    best_ask: float | None,
    last_price: float | None,
    past_hint_outcomes: list[dict] | None = None,
    market_trades: list[dict] | None = None,
    peer_views: list[dict] | None = None,
) -> str:
    payload: dict = {
        "recent_pnl": [round(float(x), 4) for x in recent_pnl[-20:]],
        "recent_trades": [_compact_own_trade(t) for t in recent_trades[-10:]],
        "soc_frac": round(float(soc_frac), 3),
        "best_bid": None if best_bid is None else round(float(best_bid), 4),
        "best_ask": None if best_ask is None else round(float(best_ask), 4),
        "last_price": None if last_price is None else round(float(last_price), 4),
    }
    if past_hint_outcomes:
        payload["past_hint_outcomes"] = [
            _compact_outcome(r) for r in past_hint_outcomes[-MAX_OUTCOMES:]
        ]
    if market_trades:
        payload["recent_market_trades"] = market_trades[-MAX_MKT_TRADES:]
    if peer_views:
        payload["peer_llm_views"] = peer_views[:MAX_PEER]
    return json.dumps(
        payload,
        ensure_ascii=False,
        # Trade records carry datetime/Decimal values straight from the runner.
        default=str,
    )


def parse_hints(content: str) -> ReflectionHints:
    """Be liberal about whitespace / surrounding prose — extract the first valid
    JSON object — but raise HintParseError when nothing usable is found, so the
    caller records the round-trip as a *failure* (with the raw snippet) instead
    of silently logging a garbage response as a successful neutral reflection.
    """
    text = content.strip()
    # Strip markdown code fences if the model added them despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        # drop leading "json\n" if present
        if text.lower().startswith("json"):
            text = text[4:].lstrip("\n")
    data = _first_json_object(text)
    if data is None:
        raise HintParseError(f"no JSON object in LLM response: {content[:160]!r}")
    try:
        return ReflectionHints(
            price_adjust=float(data.get("price_adjust", 0.0)),
            qty_scale=float(data.get("qty_scale", 1.0)),
            rationale=str(data.get("rationale", "")),
            lesson=str(data.get("lesson", "")),
        ).clamped()
    except (TypeError, ValueError) as e:
        raise HintParseError(f"hints object has non-numeric fields: {data!r}") from e


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
