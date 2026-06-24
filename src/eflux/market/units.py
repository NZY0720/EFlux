"""Monetary unit conventions for the market layer.

Energy *prices* in EFlux are quoted in $/MWh so the internal P2P book lines up
with real CAISO LMPs on a single axis (``price_ref`` defaults to 50 ≈ a typical
SP15 LMP). Energy *quantities*, however, are in kWh — natural for residential
DERs, where a 30 kWh home battery is the unit of intuition.

Settlement therefore computes cash as ``price [$/MWh] * qty [kWh]``, whose raw
product is 1000x a true-dollar figure (1 MWh = 1000 kWh). The simulator keeps
that raw internal scale end-to-end on purpose — agent PnL, the online-PPO step
reward, and the warm-started checkpoints are all calibrated to it — so rescaling
mid-stack would silently invalidate the learned value functions.

To stay honest where money is labelled "$", the conversion to real USD happens
only at the API serialization boundary via :func:`internal_cash_to_usd`.
"""

from __future__ import annotations

from decimal import Decimal

KWH_PER_MWH = Decimal("1000")


def internal_cash_to_usd(value: Decimal) -> Decimal:
    """Convert an internal cash/PnL figure (price $/MWh x qty kWh) to USD."""
    return value / KWH_PER_MWH
