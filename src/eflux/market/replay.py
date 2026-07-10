"""Deterministic conservation checks over the persisted V2 market audit stream."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class ReplayReport:
    event_count: int
    trade_count: int
    delivery_count: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def replay_and_verify(rows: list[Any], *, tolerance: float = 1e-9) -> ReplayReport:
    """Verify sequence, trade cash conservation, and delivery energy identities."""

    ordered = sorted(rows, key=lambda row: int(_field(row, "sequence_no")))
    errors: list[str] = []
    sequences = [int(_field(row, "sequence_no")) for row in ordered]
    if len(sequences) != len(set(sequences)):
        errors.append("duplicate audit sequence number")
    if sequences and sequences != list(range(sequences[0], sequences[0] + len(sequences))):
        errors.append("audit sequence has a gap")

    trade_refs: set[str] = set()
    transfer_totals: dict[tuple[str, str], Decimal] = {}
    transfer_counts: dict[tuple[str, str], int] = {}
    delivery_count = 0
    for row in ordered:
        kind = str(_field(row, "kind"))
        reference = _field(row, "reference_id")
        payload = _field(row, "payload")
        if kind == "trade":
            trade_refs.add(str(reference))
        elif kind == "ledger.entry" and payload.get("category") in {"trade", "imbalance"}:
            key = (str(payload["category"]), str(reference))
            transfer_totals[key] = transfer_totals.get(key, Decimal("0")) + Decimal(
                str(payload["amount_usd"])
            )
            transfer_counts[key] = transfer_counts.get(key, 0) + 1
        elif kind == "delivery.settled":
            delivery_count += 1
            physical = float(payload["physical_net_injection_kwh"])
            contracted = float(payload["contracted_net_injection_kwh"])
            imbalance = float(payload["imbalance_kwh"])
            if not all(math.isfinite(value) for value in (physical, contracted, imbalance)):
                errors.append(f"delivery {reference} contains non-finite energy")
            elif abs((physical - contracted) - imbalance) > tolerance:
                errors.append(f"delivery {reference} violates physical-contract=imbalance")

    for key, total in transfer_totals.items():
        if total != 0:
            errors.append(f"{key[0]} transfer {key[1]} does not conserve cash: {total}")
    for trade_ref in trade_refs:
        key = ("trade", trade_ref)
        if transfer_counts.get(key) != 2:
            errors.append(f"trade {trade_ref} does not have exactly two ledger entries")

    return ReplayReport(
        event_count=len(ordered),
        trade_count=len(trade_refs),
        delivery_count=delivery_count,
        errors=tuple(errors),
    )


def _field(row: Any, name: str):
    return row[name] if isinstance(row, dict) else getattr(row, name)
