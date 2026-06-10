"""Prompt templates + response parsing for the reflective agent.

The LLM is asked to play strategy advisor over a sliding window of recent activity.
It returns a small JSON with bounded adjustments — never raw orders — so a bad
response can't blow up the matching engine.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a trading-strategy advisor for a Virtual Power Plant (VPP) participating in a
continuous double auction electricity market. Your job is to nudge a baseline
truthful-pricing strategy based on recent market and self performance.

The market settles in real time with a CDA limit order book. Sellers post asks,
buyers post bids; orders cross when bid ≥ ask, trading at the resting order's price.

You will receive:
  - the last N tick PnL deltas (your own)
  - the last K trades that touched your VPP
  - your current SOC (state of charge, 0–1)
  - the current best bid/ask and last trade price

Return ONLY a JSON object with these keys (no commentary, no markdown fences):
  {
    "price_adjust": <float in [-0.20, 0.20]>,    // multiplicative offset, e.g. 0.05 = bid 5% higher / ask 5% lower
    "qty_scale":    <float in [0.5, 1.5]>,       // multiplicative scaling of base order qty
    "rationale":    "<short string, <=120 chars>"
  }

Stay within the numerical bounds. If unsure, return zeros / ones.
"""


@dataclass
class ReflectionHints:
    price_adjust: float = 0.0
    qty_scale: float = 1.0
    rationale: str = ""

    def clamped(self) -> ReflectionHints:
        return ReflectionHints(
            price_adjust=max(-0.20, min(0.20, float(self.price_adjust))),
            qty_scale=max(0.5, min(1.5, float(self.qty_scale))),
            rationale=str(self.rationale)[:120],
        )


def build_user_message(
    *,
    recent_pnl: list[float],
    recent_trades: list[dict],
    soc_frac: float,
    best_bid: float | None,
    best_ask: float | None,
    last_price: float | None,
) -> str:
    return json.dumps(
        {
            "recent_pnl": [round(float(x), 4) for x in recent_pnl[-20:]],
            "recent_trades": recent_trades[-10:],
            "soc_frac": round(float(soc_frac), 3),
            "best_bid": None if best_bid is None else round(float(best_bid), 4),
            "best_ask": None if best_ask is None else round(float(best_ask), 4),
            "last_price": None if last_price is None else round(float(last_price), 4),
        },
        ensure_ascii=False,
        # Trade records carry datetime/Decimal values straight from the runner.
        default=str,
    )


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_hints(content: str) -> ReflectionHints:
    """Be liberal about whitespace / surrounding prose — extract the first {...} block."""
    text = content.strip()
    # Strip markdown code fences if the model added them despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        # drop leading "json\n" if present
        if text.lower().startswith("json"):
            text = text[4:].lstrip("\n")
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        log.warning("LLM response had no JSON object: %r", content[:200])
        return ReflectionHints()
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        log.warning("LLM JSON decode failed: %r", content[:200])
        return ReflectionHints()
    return ReflectionHints(
        price_adjust=float(data.get("price_adjust", 0.0)),
        qty_scale=float(data.get("qty_scale", 1.0)),
        rationale=str(data.get("rationale", "")),
    ).clamped()
