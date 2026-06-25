"""Static CAISO reference price (the trailing-month LMP mean).

This is the *valuation anchor* the cost-based agents calibrate their `price_ref` to, and the
*normalization scale* the PPO encoding divides prices by. It is deliberately a **fixed**
number per run — the trailing-month mean — not the live tick:

- Anchoring the agents' cost basis to the live LMP would erase the live-price-vs-cost timing
  signal (a truthful/PPO agent trades precisely on that spread).
- Dividing the PPO observation by a *live* scale would collapse every price ratio (mid/ref,
  fair_buy/ref, …) to ~1.0 and blind the policy to the price level — destroying the signal
  training on real data is meant to teach.

So we compute the mean once, cache it, and treat it as a constant. CAISO unreachable →
fall back to the caller's default (the legacy 50 $/MWh) so offline / CI runs still work.

Reuses the same DAM LMP history fetch + parquet cache as PPO training
(`agents.ppo.training_data`), keyed by node + trailing window, so a scenario load is cheap.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from eflux.config import PROJECT_ROOT, get_settings
from eflux.data.electricity_market import CaisoOasisClient

log = logging.getLogger(__name__)


def _mean_cache_path(cache_dir: Path, node: str, start: date, end: date) -> Path:
    safe = node.replace("/", "_")
    return cache_dir / f"refmean_{safe}_{start.isoformat()}_{end.isoformat()}.txt"


@lru_cache(maxsize=8)
def _reference_mean(node: str, days: int, end_ord: int) -> float | None:
    """Trailing-`days` CAISO LMP mean ending at `end_ord` (a date ordinal, so the lru_cache
    key is stable within a day). None when no price could be fetched. Cached to a tiny text
    file next to the PPO training cache so repeat loads don't re-hit OASIS."""
    end_d = date.fromordinal(end_ord)
    start_d = end_d - timedelta(days=days)
    cache_dir = PROJECT_ROOT / "data" / "cache" / "training"
    cache_dir.mkdir(parents=True, exist_ok=True)
    mean_path = _mean_cache_path(cache_dir, node, start_d, end_d)
    if mean_path.exists():
        try:
            return float(mean_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pass

    start = datetime(start_d.year, start_d.month, start_d.day, tzinfo=UTC)
    end = datetime(end_d.year, end_d.month, end_d.day, tzinfo=UTC)
    try:
        rows = CaisoOasisClient().fetch_lmp_history_sync(node=node, start=start, end=end)
    except Exception:
        log.exception("CAISO reference-price fetch failed for %s", node)
        return None
    prices = [float(r.price) for r in rows if r.price is not None]
    if not prices:
        log.warning("CAISO reference-price fetch returned no rows for %s — using fallback", node)
        return None
    mean = sum(prices) / len(prices)
    try:
        mean_path.write_text(f"{mean:.6f}", encoding="utf-8")
    except OSError:
        log.exception("CAISO reference-price cache write failed: %s", mean_path)
    log.info("CAISO reference price (%dd mean, %s): %.2f $/MWh over %d points", days, node, mean, len(prices))
    return mean


def caiso_reference_price(*, default: float = 50.0, days: int | None = None, node: str | None = None) -> float:
    """Trailing-month CAISO LMP mean (the fixed valuation anchor / PPO normalization scale).

    Returns `default` — with no network call — unless `price_ref_source="caiso"` and the
    external market is enabled, so the default/test path stays static and deterministic.
    Returns `default` too when CAISO is unreachable. Memoized per (node, window) for the
    process lifetime."""
    settings = get_settings()
    if settings.price_ref_source != "caiso" or not settings.external_market_enabled:
        return default
    node = node or settings.external_market_node
    days = settings.price_ref_window_days if days is None else days
    end_ord = (date.today() - timedelta(days=1)).toordinal()
    mean = _reference_mean(node, days, end_ord)
    return mean if mean is not None else default
