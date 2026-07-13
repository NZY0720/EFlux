"""Canonical EFlux Decision Trajectory Dataset v1.

The live audit stream is optimized for execution replay.  This module adds the
point-in-time observation and joins the audit envelope into ML-ready rows without
discarding no-ops, unfilled orders, gateway rejections, or physical outcomes.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from eflux.agents.base import AgentContext
from eflux.market.gateway import DecisionExecution

DATASET_SCHEMA_VERSION = "1"
_SECRET_FRAGMENTS = ("api_key", "apikey", "authorization", "password", "secret", "token")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _canonical_json(value: Any) -> str:
    # Keep the artifact encoding aligned with evidence manifests without importing
    # the evaluation package (which imports the simulator and would form a cycle).
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def redact_secrets(value: Any) -> Any:
    """Recursively redact common secret-bearing fields before artifact publication."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if any(fragment in str(key).lower() for fragment in _SECRET_FRAGMENTS)
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _external_market(context: AgentContext) -> dict[str, Any] | None:
    quote = context.market.external_market
    if quote is None:
        return None
    age = max(0.0, (context.state.sim_ts.astimezone(UTC) - quote.fetched_at).total_seconds())
    return {
        "region": quote.region,
        "node": quote.node,
        "raw_lmp_usd_per_mwh": str(quote.raw_lmp),
        "import_price_usd_per_mwh": str(quote.import_price),
        "export_price_usd_per_mwh": str(quote.export_price),
        "interval_start": None
        if quote.interval_start is None
        else quote.interval_start.isoformat(),
        "interval_end": None if quote.interval_end is None else quote.interval_end.isoformat(),
        "fetched_at": quote.fetched_at.isoformat(),
        "quote_age_sec": age,
        "status": quote.status,
        "source": quote.source,
    }


def serialize_agent_context(context: AgentContext) -> dict[str, Any]:
    """Serialize exactly what was visible when the agent decided, with units explicit."""

    primary = context.primary_interval
    projected = (
        context.state.pending_net_kwh
        if context.projected_net_kwh is None
        else context.projected_net_kwh
    )
    residual = projected - context.contracted_net_kwh - context.open_orders_net_kwh
    observation = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "sim_ts": context.state.sim_ts.astimezone(UTC).isoformat(),
        "participant_id": context.vpp_id,
        "market": {
            "mode": context.market.market_mode,
            "interval_id": primary.interval_id,
            "delivery_start": primary.start.isoformat(),
            "delivery_end": primary.end.isoformat(),
            "gate_closure": primary.gate_closure.isoformat(),
            "seconds_to_gate_closure": max(
                0.0, (primary.gate_closure - context.state.sim_ts.astimezone(UTC)).total_seconds()
            ),
            "best_bid_usd_per_mwh": (
                None if context.market.best_bid is None else str(context.market.best_bid)
            ),
            "best_ask_usd_per_mwh": (
                None if context.market.best_ask is None else str(context.market.best_ask)
            ),
            "last_price_usd_per_mwh": (
                None if context.market.last_price is None else str(context.market.last_price)
            ),
            "mid_price_usd_per_mwh": (
                None if context.market.mid_price is None else str(context.market.mid_price)
            ),
            "bids": [[str(price), str(qty)] for price, qty in context.market.bids],
            "asks": [[str(price), str(qty)] for price, qty in context.market.asks],
            "recent_trades": redact_secrets(context.market.recent_trades),
            "external": _external_market(context),
        },
        "portfolio": {
            "params": context.params.to_dict(),
            "soc_kwh": _safe_number(context.battery.soc_kwh),
            "soc_fraction": _safe_number(context.battery.soc_frac),
            "battery_power_limit_kw": _safe_number(context.battery.max_power_kw),
            "pv_kw": _safe_number(context.state.pv_kw),
            "wind_kw": _safe_number(context.state.wind_kw),
            "load_kw": _safe_number(context.state.load_kw),
            "net_kw": _safe_number(context.state.net_kw),
            "pending_net_kwh": _safe_number(context.state.pending_net_kwh),
            "cash_pnl_usd": str(context.state.pnl),
            "energy_bought_kwh": _safe_number(context.state.cumulative_energy_bought_kwh),
            "energy_sold_kwh": _safe_number(context.state.cumulative_energy_sold_kwh),
        },
        "commitments": {
            "projected_net_kwh": _safe_number(projected),
            "contracted_net_kwh": _safe_number(context.contracted_net_kwh),
            "open_orders_net_kwh": _safe_number(context.open_orders_net_kwh),
            "residual_exposure_kwh": _safe_number(residual),
            "open_orders": [
                {
                    "order_id": order.order_id,
                    "side": order.side,
                    "price_usd_per_mwh": str(order.price),
                    "remaining_qty_kwh": str(order.remaining_qty),
                    "age_ticks": order.age_ticks,
                    "dispatched": order.dispatched,
                }
                for order in context.open_orders
            ],
        },
        "history": {
            "gateway_rejections_total": _safe_number(context.risk_rejections_total),
            "realized_abs_imbalance_kwh_total": _safe_number(
                context.realized_imbalance_abs_kwh_total
            ),
            "silence_ticks": context.silence_ticks,
            "silence_reasons": dict(context.silence_reasons or {}),
        },
        "forecast": None if context.forecast is None else context.forecast.to_dict(),
        "visible_products": [
            {
                "interval_id": interval.interval_id,
                "start": interval.start.isoformat(),
                "end": interval.end.isoformat(),
                "gate_closure": interval.gate_closure.isoformat(),
            }
            for interval in context.delivery_intervals
        ],
    }
    return redact_secrets(observation)


def serialize_decision_execution(
    execution: DecisionExecution,
    *,
    participant_id: int,
    mid_price: Decimal | None,
    fallback: bool,
    grid_participant_id: int | None = None,
) -> dict[str, Any]:
    fills = []
    slippage_usd = Decimal("0")
    for trade in execution.trades:
        side = "buy" if trade.buy_vpp_id == participant_id else "sell"
        if mid_price is not None:
            signed = trade.price - mid_price if side == "buy" else mid_price - trade.price
            slippage_usd += signed * trade.qty / Decimal("1000")
        fills.append(
            {
                "trade_id": trade.trade_id,
                "interval_id": trade.interval.interval_id,
                "side": side,
                "price_usd_per_mwh": str(trade.price),
                "qty_kwh": str(trade.qty),
                "buy_order_id": trade.buy_order_id,
                "sell_order_id": trade.sell_order_id,
                "venue": (
                    "grid"
                    if grid_participant_id is not None
                    and grid_participant_id in (trade.buy_vpp_id, trade.sell_vpp_id)
                    else "peer"
                ),
            }
        )
    return {
        "accepted_order_ids": list(execution.accepted_order_ids),
        "cancelled_order_ids": list(execution.cancelled_order_ids),
        "rejections": [
            {"reason": rejected.reason, "request": {"side": rejected.request.side}}
            for rejected in execution.rejected
        ],
        "fills": fills,
        "unfilled_order_count": max(0, len(execution.accepted_order_ids) - len(fills)),
        "slippage_usd": str(slippage_usd),
        "fallback": fallback,
    }


def _event_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, Mapping):
        return dict(event)
    return {
        "sequence_no": event.sequence_no,
        "kind": event.kind,
        "interval_id": event.interval_id,
        "participant_id": event.participant_id,
        "reference_id": event.reference_id,
        "sim_ts": event.sim_ts.isoformat(),
        "payload": event.payload,
    }


def build_trajectory_rows(events: Iterable[Any]) -> list[dict[str, Any]]:
    """Join audit events into one row per decision, including no-op decisions."""

    materialized = sorted(
        (_event_dict(event) for event in events), key=lambda row: row["sequence_no"]
    )
    decisions: dict[str, dict[str, Any]] = {}
    per_participant: dict[int, list[str]] = defaultdict(list)
    delivery_outcomes: dict[tuple[int, str], dict[str, Any]] = {}
    for event in materialized:
        kind = event["kind"]
        payload = dict(event.get("payload") or {})
        reference = str(event.get("reference_id") or payload.get("decision_id") or "")
        participant = event.get("participant_id")
        if kind == "decision.received" and reference and participant is not None:
            row = {
                "schema_version": DATASET_SCHEMA_VERSION,
                "decision_id": reference,
                "participant_id": int(participant),
                "sim_ts": str(event["sim_ts"]),
                "observation": payload.get("observation"),
                "action": {
                    "rationale": payload.get("rationale"),
                    "orders": payload.get("orders", []),
                    "cancels": payload.get("cancels", []),
                    "replaces": payload.get("replaces", []),
                    "llm_guidance": payload.get("llm_guidance"),
                    "policy_sample": payload.get("policy_sample"),
                    "is_noop": not any(
                        payload.get(field) for field in ("orders", "cancels", "replaces")
                    ),
                },
                "execution_result": None,
                "outcome": {},
                "next_observation": None,
            }
            decisions[reference] = row
            per_participant[int(participant)].append(reference)
        elif kind == "gateway.accepted" and reference in decisions:
            decisions[reference]["execution_result"] = payload.get("execution_result", payload)
        elif kind == "gateway.rejected" and reference in decisions:
            execution = decisions[reference].setdefault("execution_result", {}) or {}
            execution["rejections"] = payload.get("rejections", [])
            decisions[reference]["execution_result"] = execution
        elif kind == "delivery.settled" and participant is not None and event.get("interval_id"):
            delivery_outcomes[(int(participant), str(event["interval_id"]))] = payload

    for participant, ids in per_participant.items():
        for index, decision_id in enumerate(ids):
            row = decisions[decision_id]
            observation = row.get("observation") or {}
            interval_id = (observation.get("market") or {}).get("interval_id")
            if interval_id:
                row["outcome"] = delivery_outcomes.get((participant, str(interval_id)), {})
            if index + 1 < len(ids):
                row["next_observation"] = decisions[ids[index + 1]].get("observation")
            validate_trajectory_record(row)
    return [decisions[key] for key in sorted(decisions, key=lambda item: decisions[item]["sim_ts"])]


def validate_trajectory_record(record: Mapping[str, Any]) -> None:
    required = (
        "schema_version",
        "decision_id",
        "participant_id",
        "sim_ts",
        "observation",
        "action",
        "execution_result",
        "outcome",
    )
    missing = [field for field in required if record.get(field) is None]
    if missing:
        raise ValueError(f"trajectory record missing required fields: {missing}")
    if record["schema_version"] != DATASET_SCHEMA_VERSION:
        raise ValueError(f"unsupported trajectory schema: {record['schema_version']!r}")


def _secret_scan(value: Any, *, path: str = "record") -> tuple[list[str], int]:
    """Return unredacted secret locations and the number of explicit redactions."""

    violations: list[str] = []
    redacted = 0
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            secret_key = any(fragment in key.lower() for fragment in _SECRET_FRAGMENTS)
            if secret_key:
                if child == "[REDACTED]":
                    redacted += 1
                    continue
                else:
                    violations.append(child_path)
            child_violations, child_redacted = _secret_scan(child, path=child_path)
            violations.extend(child_violations)
            redacted += child_redacted
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            child_violations, child_redacted = _secret_scan(child, path=f"{path}[{index}]")
            violations.extend(child_violations)
            redacted += child_redacted
    elif isinstance(value, str):
        if value == "[REDACTED]":
            redacted += 1
        elif any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            violations.append(path)
    return violations, redacted


def validate_publishable_trajectory_record(record: Mapping[str, Any]) -> dict[str, int]:
    """Validate one canonical row using the actual data, not manifest assertions.

    The required negative-example fields must be present even when their value is
    zero/empty.  This distinguishes a true no-op or zero-rejection observation from
    an exporter that silently dropped those outcomes.
    """

    validate_trajectory_record(record)
    observation = record["observation"]
    action = record["action"]
    execution = record["execution_result"]
    outcome = record["outcome"]
    if not isinstance(observation, Mapping) or not observation:
        raise ValueError("trajectory observation must be a non-empty object")
    if not isinstance(observation.get("market"), Mapping):
        raise ValueError("trajectory observation.market is required")
    if not isinstance(action, Mapping):
        raise ValueError("trajectory action must be an object")
    for field in ("orders", "cancels", "replaces"):
        if not isinstance(action.get(field), list):
            raise ValueError(f"trajectory action.{field} must be a list")
    if not isinstance(action.get("is_noop"), bool):
        raise ValueError("trajectory action.is_noop must be a boolean")
    inferred_noop = not any(action[field] for field in ("orders", "cancels", "replaces"))
    if action["is_noop"] is not inferred_noop:
        raise ValueError("trajectory action.is_noop contradicts the recorded action")
    if not isinstance(execution, Mapping):
        raise ValueError("trajectory execution_result must be an object")
    for field in (
        "accepted_order_ids",
        "cancelled_order_ids",
        "rejections",
        "fills",
    ):
        if not isinstance(execution.get(field), list):
            raise ValueError(f"trajectory execution_result.{field} must be a list")
    unfilled = execution.get("unfilled_order_count")
    if not isinstance(unfilled, int) or isinstance(unfilled, bool) or unfilled < 0:
        raise ValueError(
            "trajectory execution_result.unfilled_order_count must be a non-negative integer"
        )
    if not isinstance(execution.get("fallback"), bool):
        raise ValueError("trajectory execution_result.fallback must be a boolean")
    if not isinstance(outcome, Mapping) or not outcome:
        raise ValueError("trajectory outcome must contain the delivery result")

    violations, redacted = _secret_scan(record)
    if violations:
        locations = ", ".join(violations[:5])
        raise ValueError(f"trajectory contains unredacted secret material at: {locations}")
    return {
        "no_op_count": int(action["is_noop"]),
        "rejection_count": len(execution["rejections"]),
        "unfilled_order_count": unfilled,
        "fill_count": len(execution["fills"]),
        "fallback_count": int(execution["fallback"]),
        "redacted_value_count": redacted,
    }


def _open_trajectory_text(path: Path):
    """Open JSONL or gzip JSONL based on file magic, independent of its suffix."""

    raw = path.open("rb")
    magic = raw.read(2)
    raw.seek(0)
    if magic == b"\x1f\x8b":
        return io.TextIOWrapper(gzip.GzipFile(fileobj=raw), encoding="utf-8")
    return io.TextIOWrapper(raw, encoding="utf-8")


def inspect_trajectory_artifact(path: Path | str) -> dict[str, Any]:
    """Stream and validate a trajectory artifact, deriving trustworthy completeness."""

    source = Path(path)
    totals = {
        "no_op_count": 0,
        "rejection_count": 0,
        "unfilled_order_count": 0,
        "fill_count": 0,
        "fallback_count": 0,
        "redacted_value_count": 0,
    }
    row_count = 0
    try:
        with _open_trajectory_text(source) as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ValueError(f"trajectory line {line_number} is blank")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"trajectory line {line_number} is not valid JSON") from exc
                if not isinstance(record, Mapping):
                    raise ValueError(f"trajectory line {line_number} must be a JSON object")
                try:
                    counts = validate_publishable_trajectory_record(record)
                except ValueError as exc:
                    raise ValueError(f"trajectory line {line_number}: {exc}") from exc
                for key, count in counts.items():
                    totals[key] += count
                row_count += 1
    except (gzip.BadGzipFile, OSError, UnicodeError) as exc:
        raise ValueError("trajectory artifact is unreadable") from exc
    if row_count == 0:
        raise ValueError("trajectory artifact contains no records")
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "row_count": row_count,
        "completeness": {
            "observation": True,
            "action": True,
            "execution_result": True,
            "outcome": True,
            "no_op": True,
            "unfilled_orders": True,
            "gateway_rejections": True,
        },
        "observed": totals,
        "redaction": {
            "status": "verified",
            "unredacted_secret_count": 0,
            "redacted_value_count": totals["redacted_value_count"],
        },
    }


def export_trajectory_jsonl_gz(
    records: Iterable[Mapping[str, Any]], path: Path | str
) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with gzip.open(target, "wt", encoding="utf-8", newline="\n") as handle:
        for record in records:
            validate_trajectory_record(record)
            handle.write(_canonical_json(redact_secrets(dict(record))))
            handle.write("\n")
            count += 1
    return {
        "path": str(target),
        "row_count": count,
        "size_bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "schema_version": DATASET_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
