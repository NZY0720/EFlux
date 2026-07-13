"""Deterministic conservation checks over the persisted V2 market audit stream."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class ReplayReport:
    event_count: int
    trade_count: int
    delivery_count: int
    errors: tuple[str, ...]
    state_sha256: str = ""
    decision_count: int = 0
    order_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class ReplayOrder:
    order_id: int
    participant_id: int | None = None
    interval_id: str | None = None
    side: str | None = None
    purpose: str | None = None
    price: str | None = None
    qty_kwh: str | None = None
    remaining_qty_kwh: str | None = None
    status: str = "accepted"
    decision_id: str | None = None
    filled_kwh: Decimal = Decimal("0")
    direct_trade_cash_usd: Decimal = Decimal("0")
    trade_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReplayState:
    """Semantic state rebuilt only from the persisted audit envelope."""

    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    orders: dict[int, ReplayOrder] = field(default_factory=dict)
    trades: dict[str, dict[str, Any]] = field(default_factory=dict)
    participant_balances: dict[int, Decimal] = field(default_factory=dict)
    ledger_entries: list[dict[str, Any]] = field(default_factory=list)
    deliveries: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_sequence_no: int = 0


def _decimal_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _decimal_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decimal_json(item) for item in value]
    return value


def replay_state(rows: list[Any]) -> ReplayState:
    """Reduce the complete decision/order/trade/ledger/delivery stream into state."""

    state = ReplayState()
    for row in sorted(rows, key=lambda item: int(_field(item, "sequence_no"))):
        sequence = int(_field(row, "sequence_no"))
        state.last_sequence_no = sequence
        kind = str(_field(row, "kind"))
        reference = str(_optional_field(row, "reference_id") or "")
        participant_raw = _optional_field(row, "participant_id")
        participant_id = int(participant_raw) if participant_raw is not None else None
        payload = dict(_field(row, "payload") or {})
        if kind == "decision.received":
            decision_id = str(payload.get("decision_id") or reference)
            state.decisions[decision_id] = {
                "participant_id": participant_id,
                **payload,
            }
        elif kind == "gateway.accepted":
            decision_id = str(payload.get("decision_id") or reference)
            decision = state.decisions.get(decision_id, {})
            accepted_orders = payload.get("accepted_orders")
            if not isinstance(accepted_orders, list):
                requests = [
                    *(item.get("replacement", {}) for item in decision.get("replaces", [])),
                    *decision.get("orders", []),
                ]
                accepted_orders = [
                    {"order_id": raw_order_id, **(requests[index] if index < len(requests) else {})}
                    for index, raw_order_id in enumerate(payload.get("accepted_order_ids", []))
                ]
            for accepted in accepted_orders:
                order_id = int(accepted["order_id"])
                request = accepted
                state.orders.setdefault(
                    order_id,
                    ReplayOrder(
                        order_id=order_id,
                        participant_id=participant_id,
                        interval_id=request.get("interval_id"),
                        side=request.get("side"),
                        purpose=request.get("purpose"),
                        price=request.get("price"),
                        qty_kwh=request.get("qty_kwh"),
                        remaining_qty_kwh=request.get("qty_kwh"),
                        decision_id=decision_id,
                    ),
                )
            for raw_order_id in payload.get("cancelled_order_ids", []):
                order = state.orders.setdefault(int(raw_order_id), ReplayOrder(int(raw_order_id)))
                order.status = "cancelled"
                order.remaining_qty_kwh = "0"
        elif kind in {"order.submitted", "order.cancelled"}:
            order_id = int(payload.get("order_id") or reference)
            order = state.orders.setdefault(order_id, ReplayOrder(order_id))
            order.participant_id = (
                participant_id if participant_id is not None else payload.get("vpp_id")
            )
            order.interval_id = _optional_field(row, "interval_id")
            order.side = payload.get("side", order.side)
            order.purpose = payload.get("purpose", order.purpose)
            order.price = payload.get("price", order.price)
            order.qty_kwh = payload.get("qty_kwh", order.qty_kwh)
            order.remaining_qty_kwh = payload.get(
                "remaining_qty_kwh", order.remaining_qty_kwh
            )
            order.status = "cancelled" if kind == "order.cancelled" else "open"
        elif kind == "trade":
            trade_id = str(payload.get("trade_id") or reference)
            state.trades[trade_id] = payload
            qty = Decimal(str(payload.get("qty", "0")))
            cash = Decimal(str(payload.get("price", "0"))) * qty / Decimal("1000")
            for side, id_key in (("buy", "buy_order_id"), ("sell", "sell_order_id")):
                raw_order_id = payload.get(id_key)
                if raw_order_id is None:
                    continue
                order_id = int(raw_order_id)
                order = state.orders.setdefault(order_id, ReplayOrder(order_id))
                order.filled_kwh += qty
                order.direct_trade_cash_usd += cash if side == "sell" else -cash
                order.trade_ids.append(trade_id)
                if order.qty_kwh is not None:
                    remaining = max(Decimal("0"), Decimal(order.qty_kwh) - order.filled_kwh)
                    order.remaining_qty_kwh = str(remaining)
                    order.status = "filled" if remaining == 0 else "partially_filled"
        elif kind == "ledger.entry":
            entry = {
                "sequence_no": sequence,
                "participant_id": participant_id,
                "reference_id": reference,
                **payload,
            }
            state.ledger_entries.append(entry)
            if participant_id is not None:
                state.participant_balances[participant_id] = state.participant_balances.get(
                    participant_id, Decimal("0")
                ) + Decimal(str(payload.get("amount_usd", "0")))
        elif kind == "delivery.settled":
            key = f"{participant_id}:{_optional_field(row, 'interval_id') or reference}"
            state.deliveries[key] = {
                "participant_id": participant_id,
                "interval_id": _optional_field(row, "interval_id") or reference,
                **payload,
            }
    return state


def replay_state_sha256(state_or_rows: ReplayState | list[Any]) -> str:
    state = state_or_rows if isinstance(state_or_rows, ReplayState) else replay_state(state_or_rows)
    payload = _decimal_json(asdict(state))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def explain_order(state_or_rows: ReplayState | list[Any], order_id: int) -> dict[str, Any]:
    """Return direct order economics and explicitly contextual downstream effects."""

    state = state_or_rows if isinstance(state_or_rows, ReplayState) else replay_state(state_or_rows)
    order = state.orders.get(order_id)
    if order is None:
        raise KeyError(f"order {order_id} not present in audit stream")
    related_ledger = [
        entry
        for entry in state.ledger_entries
        if str(entry.get("reference_id")) in set(order.trade_ids)
    ]
    delivery_key = f"{order.participant_id}:{order.interval_id}"
    return _decimal_json(
        {
            "order": asdict(order),
            "direct_trade_ledger": related_ledger,
            "interval_context": state.deliveries.get(delivery_key),
            "attribution_note": (
                "Trade cash is directly attributable to this order. Delivery imbalance, "
                "degradation and settlement are interval-level context and are not assigned "
                "to one order without a counterfactual model."
            ),
        }
    )


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

    state = replay_state(rows)
    return ReplayReport(
        event_count=len(ordered),
        trade_count=len(trade_refs),
        delivery_count=delivery_count,
        errors=tuple(errors),
        state_sha256=replay_state_sha256(state),
        decision_count=len(state.decisions),
        order_count=len(state.orders),
    )


def _field(row: Any, name: str):
    return row[name] if isinstance(row, dict) else getattr(row, name)


def _optional_field(row: Any, name: str):
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)
