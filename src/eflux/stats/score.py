"""Score v1 — endowment-normalized performance, computed at query time (never stored).

Raw PnL rewards whoever brought the biggest battery. Score v1 divides PnL by the
revenue the endowment could earn running every asset flat-out at the session's
reference price, making entrants with different DER portfolios and observation
windows comparable:

    power_scale_kw    = max(0.5, pv + wind + battery_kw + gas + load)
    revenue_scale_usd = price_ref [$/MWh] * power_scale_kw * elapsed_h / 1000
    score             = pnl_usd / revenue_scale_usd

Dimensionless; interpret as "fraction of the endowment's flat-out revenue captured
as profit". Floors (0.5 kW, 1 h) keep degenerate endowments and just-deployed agents
from producing absurd ratios. The formula lives in this one pure function so it can
evolve without rewriting any stored rows.
"""

from __future__ import annotations

from collections.abc import Mapping

# Floors: a zero-asset endowment or a seconds-old agent must not divide by ~0.
MIN_POWER_SCALE_KW = 0.5
MIN_ELAPSED_H = 1.0

_POWER_FIELDS = ("pv_kw_peak", "wind_kw_rated", "battery_kw_max", "gas_kw_max", "load_kw_base")


def power_scale_kw(params: Mapping[str, float]) -> float:
    """Total nameplate power of the endowment (kW), floored at MIN_POWER_SCALE_KW."""
    total = sum(float(params.get(field, 0.0) or 0.0) for field in _POWER_FIELDS)
    return max(MIN_POWER_SCALE_KW, total)


def revenue_scale_usd(params: Mapping[str, float], price_ref: float, elapsed_h: float) -> float:
    """The score denominator: revenue of the endowment running flat-out at price_ref.

    Exposed separately so multi-session aggregation can sum denominators
    (all-time score = sum pnl / sum scale) without re-deriving the formula.
    """
    hours = max(MIN_ELAPSED_H, elapsed_h)
    return max(1e-9, float(price_ref)) * power_scale_kw(params) * hours / 1000.0


def compute_score(
    pnl_usd: float,
    params: Mapping[str, float],
    price_ref: float,
    elapsed_h: float,
) -> float:
    """Endowment- and duration-normalized score (see module docstring).

    params carries the endowment power fields (extra keys ignored); price_ref is the
    session's reference price in $/MWh; elapsed_h the identity's observed sim-hours.
    """
    return float(pnl_usd) / revenue_scale_usd(params, price_ref, elapsed_h)
