"""Gateway-backed historical prove-out execution.

The strategy chooses orders, but every accepted quantity is reserved, matched,
physically delivered and settled by the production V2 Simulator/Gateway path.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder

from eflux.agents.base import AgentContext, BaseAgent
from eflux.agents.decision import AgentDecision, OrderRequest
from eflux.bridge import InMemoryBus
from eflux.config import get_settings
from eflux.data.electricity_market import synthetic_quote
from eflux.evaluation.manifest import DataArtifact, RunManifest, build_manifest
from eflux.market.delivery import OrderPurpose
from eflux.market.ledger import LedgerCategory
from eflux.market.products import TimeInForce
from eflux.market.replay import explain_order, replay_and_verify, replay_state
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams


@dataclass(frozen=True, slots=True)
class ProveOutExecution:
    report: dict[str, Any]
    manifest: RunManifest
    evidence: dict[str, Any]


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class HistoricalArbitrageAgent(BaseAgent):
    """Chronological battery/solar policy for the Prove-out managed track."""

    def __init__(self, params: dict[str, float | int]) -> None:
        self.lookback_hours = int(params["lookback_hours"])
        self.minimum_history_hours = int(params["minimum_history_hours"])
        self.charge_percentile = float(params["charge_percentile"])
        self.discharge_percentile = float(params["discharge_percentile"])
        self._hourly_observations: list[tuple[datetime, float]] = []

    def decide(self, ctx: AgentContext) -> AgentDecision:
        quote = ctx.market.external_market
        if quote is None:
            return AgentDecision.hold("historical quote unavailable")
        current_price = float(quote.raw_lmp)
        observed_hour = ctx.market.sim_ts.astimezone(UTC).replace(
            minute=0, second=0, microsecond=0
        )
        # Add one observation per source hour. Thresholds use only observations
        # from earlier hours, so repeating an hourly LMP over 5-minute products
        # never gives that hour twelve votes.
        prior = [price for _, price in self._hourly_observations[-self.lookback_hours :]]
        orders: list[OrderRequest] = []

        projected = float(ctx.projected_net_kwh or 0.0)
        if projected > 1e-9:
            orders.append(
                OrderRequest(
                    side="sell",
                    price=quote.export_price,
                    qty_kwh=Decimal(str(projected)),
                    interval=ctx.primary_interval,
                    purpose=OrderPurpose.BALANCE,
                    time_in_force=TimeInForce.FILL_OR_KILL,
                    client_ref=f"solar:{ctx.primary_interval.start.isoformat()}",
                )
            )

        if len(prior) >= self.minimum_history_hours and ctx.battery.capacity_kwh > 0:
            low = _percentile(prior, self.charge_percentile)
            high = _percentile(prior, self.discharge_percentile)
            eta = math.sqrt(ctx.battery.eta_rt)
            terminal_budget = ctx.battery.max_power_kw * ctx.primary_interval.duration_h
            if current_price < low:
                terminal_room = max(
                    0.0, (ctx.battery.capacity_kwh - ctx.battery.soc_kwh) / eta
                )
                quantity = min(terminal_budget, terminal_room)
                if quantity >= 0.01:
                    orders.append(
                        OrderRequest(
                            side="buy",
                            price=quote.import_price,
                            qty_kwh=Decimal(str(quantity)),
                            interval=ctx.primary_interval,
                            purpose=OrderPurpose.BATTERY,
                            time_in_force=TimeInForce.FILL_OR_KILL,
                            client_ref=f"battery-charge:{ctx.primary_interval.start.isoformat()}",
                        )
                    )
            elif current_price > high:
                terminal_available = max(0.0, ctx.battery.soc_kwh * eta)
                quantity = min(terminal_budget, terminal_available)
                if quantity >= 0.01:
                    orders.append(
                        OrderRequest(
                            side="sell",
                            price=quote.export_price,
                            qty_kwh=Decimal(str(quantity)),
                            interval=ctx.primary_interval,
                            purpose=OrderPurpose.BATTERY,
                            time_in_force=TimeInForce.FILL_OR_KILL,
                            client_ref=f"battery-discharge:{ctx.primary_interval.start.isoformat()}",
                        )
                    )

        if not self._hourly_observations or self._hourly_observations[-1][0] != observed_hour:
            self._hourly_observations.append((observed_hour, current_price))
        return AgentDecision(orders=tuple(orders), rationale="historical_arbitrage")


def _price_for(points: list[Any], at: datetime) -> float:
    hour = at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    by_hour = {
        point.timestamp.astimezone(UTC).replace(minute=0, second=0, microsecond=0): float(
            point.price
        )
        for point in points
    }
    try:
        return by_hour[hour]
    except KeyError as exc:
        raise ValueError(f"historical price missing for {hour.isoformat()}") from exc


def _endowment_params(endowment: dict[str, Any]) -> VPPParams:
    battery = endowment.get("battery") or {}
    return VPPParams(
        pv_kw_peak=float(endowment.get("solar_mw", 0.0)) * 1000.0,
        battery_kwh=float(battery.get("energy_mwh", 0.0)) * 1000.0,
        battery_kw_max=float(battery.get("power_mw", 0.0)) * 1000.0,
        battery_eta_rt=float(battery.get("round_trip_efficiency", 0.9)),
        battery_initial_soc_frac=0.0,
        battery_degradation_cost_per_mwh_throughput=float(
            battery.get("cycle_cost_per_mwh", 0.0)
        ),
        load_kw_base=0.0,
        load_elasticity=0.0,
        starting_cash_usd=float(endowment.get("cash_usd", 10000.0)),
        forecast_noise_std=0.0,
    )


def _daily_rows(entries: Iterable[Any], start_date: date, end_date: date, *, tz) -> list[dict]:
    daily: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for entry in entries:
        daily[entry.occurred_at.astimezone(tz).date()] += entry.amount_usd
    rows: list[dict] = []
    day = start_date
    while day <= end_date:
        rows.append({"date": day.isoformat(), "pnl_usd": round(float(daily[day]), 6)})
        day += timedelta(days=1)
    return rows


def execute_gateway_proveout(
    *,
    prices: list[Any],
    endowment: dict[str, Any],
    strategy: dict[str, Any],
    strategy_params: dict[str, float | int],
    start_date: date,
    end_date: date,
    perfect_foresight_usd: float,
    baseline_hold_usd: float,
    data_artifact: DataArtifact,
    local_timezone,
) -> ProveOutExecution:
    if not prices:
        raise ValueError("prove-out requires historical prices")
    start = min(point.timestamp for point in prices).astimezone(UTC)
    end = max(point.timestamp for point in prices).astimezone(UTC) + timedelta(hours=1)
    settings = get_settings()
    sim = Simulator(bus=InMemoryBus(), sim_epoch=start)
    sim.market_mode = "realprice"
    sim.imbalance_settlement_enabled = True
    params = _endowment_params(endowment)
    candidate = sim.add_builtin_vpp(
        "proveout-candidate",
        params,
        HistoricalArbitrageAgent(strategy_params),
        strategy="battery_arbitrageur",
        seed=0,
    )

    transaction_fee = Decimal(str(settings.external_market_transaction_fee))
    decision_ts = start
    while decision_ts < end:
        price = Decimal(str(_price_for(prices, decision_ts)))
        sim._external_market_quote = synthetic_quote(
            region=settings.market_region,
            node=settings.external_market_node,
            price=price,
            status="real",
            source="Historical CAISO cache",
            detail="Hourly historical LMP replayed over five-minute delivery products.",
            now=decision_ts,
            transaction_fee=transaction_fee,
        )
        sim.run_interval_once(decision_ts)
        decision_ts += timedelta(minutes=5)

    sim._audit_new_ledger_entries()
    entries = [
        entry for entry in sim.gateway.ledger.entries if entry.participant_id == candidate.vpp_id
    ]
    pnl = sim.gateway.ledger.balance(candidate.vpp_id)
    breakdown = sim.gateway.ledger.breakdown(candidate.vpp_id)
    running = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for entry in entries:
        running += entry.amount_usd
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
    day_count = (end_date - start_date).days + 1
    normalizer_mw = float((endowment.get("battery") or {}).get("power_mw", 0.0)) or float(
        endowment.get("solar_mw", 0.0)
    )
    per_kw_month = (
        float(pnl) * 30.0 / (normalizer_mw * 1000.0 * day_count)
        if normalizer_mw > 0
        else 0.0
    )
    spread_capture = (
        100.0 * float(pnl) / perfect_foresight_usd if perfect_foresight_usd > 0 else None
    )
    daily = _daily_rows(entries, start_date, end_date, tz=local_timezone)
    # Per-day perfect foresight is optional evidence. The overall bound remains authoritative.
    for row in daily:
        row["spread_capture_pct"] = None

    manifest = build_manifest(
        run_type="proveout",
        market_mode="realprice",
        parameters={
            "endowment": endowment,
            "strategy": strategy,
            "window": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "delivery_interval_sec": 300,
            "price_resolution": "1h repeated over 5m products",
            "transaction_fee_usd_per_mwh": float(transaction_fee),
        },
        data=[data_artifact],
    )
    audit_rows = jsonable_encoder(list(sim._audit_buffer))
    replay = replay_and_verify(audit_rows)
    rebuilt = replay_state(audit_rows)
    order_attribution = {
        str(order_id): explain_order(rebuilt, order_id)
        for order_id, order in rebuilt.orders.items()
        if order.participant_id == candidate.vpp_id
    }
    report = {
        "pnl_usd": round(float(pnl), 6),
        "per_kw_month": round(per_kw_month, 6),
        "spread_capture_pct": round(spread_capture, 6) if spread_capture is not None else None,
        "perfect_foresight_usd": round(perfect_foresight_usd, 6),
        "baseline_hold_usd": round(baseline_hold_usd, 6),
        "max_drawdown_usd": round(float(max_drawdown), 6),
        "trades": candidate.trade_count,
        "risk_rejections": sim.risk_rejections_by_vpp.get(candidate.vpp_id, 0),
        "imbalance_penalty_usd": round(
            max(0.0, -float(breakdown.get(LedgerCategory.IMBALANCE, Decimal("0")))), 6
        ),
        "degradation_cost_usd": round(
            max(
                0.0,
                -float(breakdown.get(LedgerCategory.BATTERY_DEGRADATION, Decimal("0"))),
            ),
            6,
        ),
        "ending_soc_kwh": round(candidate.battery.soc_kwh, 6),
        "energy_bought_kwh": round(candidate.state.cumulative_energy_bought_kwh, 6),
        "energy_sold_kwh": round(candidate.state.cumulative_energy_sold_kwh, 6),
        "days": day_count,
        "daily": daily,
        "ledger_breakdown": {key.value: round(float(value), 6) for key, value in breakdown.items()},
        "evidence_id": manifest.evidence_id,
        "engine": "Simulator + TradingGatewayV2",
        "price_resolution": "hourly LMP repeated over five-minute products",
        "audit_event_count": replay.event_count,
        "replay_state_sha256": replay.state_sha256,
        "replay_verified": replay.ok,
    }
    evidence = {
        "manifest": {**manifest.model_dump(mode="json"), "evidence_id": manifest.evidence_id},
        "audit": audit_rows,
        "replay": {
            "ok": replay.ok,
            "errors": list(replay.errors),
            "event_count": replay.event_count,
            "decision_count": replay.decision_count,
            "order_count": replay.order_count,
            "trade_count": replay.trade_count,
            "delivery_count": replay.delivery_count,
            "state_sha256": replay.state_sha256,
        },
        "order_attribution": order_attribution,
        "ledger": [
            {
                "entry_id": entry.entry_id,
                "category": entry.category.value,
                "amount_usd": str(entry.amount_usd),
                "occurred_at": entry.occurred_at.isoformat(),
                "interval_id": entry.interval_id,
                "reference_id": entry.reference_id,
                "detail": entry.detail,
            }
            for entry in entries
        ],
    }
    return ProveOutExecution(report=report, manifest=manifest, evidence=evidence)
