"""Platform-derived evidence for immutable Agent Releases."""

from __future__ import annotations

import asyncio
import math
from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from statistics import fmean, pstdev
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eflux.agents.bench.run import measure_episode, run_episode
from eflux.agents.truthful import TruthfulAgent
from eflux.db.models import AgentRelease, PopulationPack, ReleaseEvaluation, VppStatSnapshot
from eflux.db.session import get_sessionmaker
from eflux.ecosystem.catalog import (
    get_standard_profile,
    list_builtin_population_packs,
)
from eflux.evaluation.manifest import content_sha256
from eflux.evaluation.paired import evaluate_paired_worlds
from eflux.market.ledger import LedgerCategory
from eflux.simulator.runner import GRID_PARTICIPANT_ID
from eflux.vpp.base import VPPParams


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _normal_mean_ci95(values: list[float]) -> list[float] | None:
    if len(values) < 2:
        return None
    mean = fmean(values)
    margin = 1.96 * pstdev(values) / math.sqrt(len(values))
    return [mean - margin, mean + margin]


def _release_payload(release: AgentRelease) -> dict[str, Any]:
    return {
        "id": release.id,
        "name": release.name,
        "version": release.version,
        "market": release.market,
        "recipe": dict(release.recipe),
        "state": dict(release.state),
        "compatibility": dict(release.compatibility),
        "environment": dict(release.environment),
        "content_sha256": release.content_sha256,
    }


def _profile(config: dict[str, Any]) -> dict[str, Any]:
    return get_standard_profile(str(config.get("profile_id") or "battery-only"))


def _hidden_population_packs() -> list[dict[str, Any]]:
    """Stable worker-only rosters used by the default formal P2P protocol."""

    definitions = (
        (
            "hidden-balanced-a",
            "Hidden balanced roster A",
            {"renewable_multiplier": 1.15, "load_multiplier": 1.05, "liquidity": "medium"},
            [
                {"strategy": "truthful", "count": 3, "profile_pool": ["battery-only"]},
                {
                    "strategy": "zip",
                    "count": 2,
                    "profile_pool": ["residential-pv-battery", "commercial-load-battery"],
                },
                {
                    "strategy": "gd",
                    "count": 2,
                    "profile_pool": ["renewable-generator", "battery-only"],
                },
                {
                    "strategy": "zero_intelligence",
                    "count": 1,
                    "profile_pool": ["industrial-flexible-load"],
                },
            ],
        ),
        (
            "hidden-stress-b",
            "Hidden stress roster B",
            {"renewable_multiplier": 0.65, "load_multiplier": 1.35, "liquidity": "low"},
            [
                {
                    "strategy": "aa",
                    "count": 2,
                    "profile_pool": ["battery-only", "commercial-load-battery"],
                },
                {
                    "strategy": "adversarial",
                    "count": 2,
                    "profile_pool": ["renewable-generator", "industrial-flexible-load"],
                },
                {
                    "strategy": "truthful",
                    "count": 3,
                    "profile_pool": ["residential-pv-battery", "battery-only"],
                },
            ],
        ),
    )
    packs = []
    for pack_id, name, scenario, roster in definitions:
        spec = {
            "schema_version": "1",
            "catalog_id": pack_id,
            "scenario": scenario,
            "roster": roster,
            "worker_hidden": True,
        }
        content = {
            "id": pack_id,
            "version": "1",
            "name": name,
            "description": "Worker-only roster used to test out-of-catalog robustness.",
            "market": "p2p",
            "spec": spec,
        }
        packs.append(
            {
                **content,
                "content_sha256": content_sha256(content),
                "worker_hidden": True,
            }
        )
    return packs


def _population_evidence(pack: dict[str, Any]) -> dict[str, Any]:
    if not pack.get("worker_hidden"):
        return pack
    return {
        "id": pack["id"],
        "version": pack["version"],
        "name": pack["name"],
        "description": pack["description"],
        "market": "p2p",
        "content_sha256": pack["content_sha256"],
        "worker_hidden": True,
        "spec": {
            "roster_disclosure": "withheld by the platform protocol",
            "roster_fingerprint": pack["content_sha256"],
        },
    }


def _historical_grid_protocol(
    config: dict[str, Any], requested_intervals: int | None
) -> tuple[list[Decimal] | None, int, dict[str, Any]]:
    start_text = config.get("window_start")
    end_text = config.get("window_end")
    if start_text is None and end_text is None:
        count = 288 if requested_intervals is None else requested_intervals
        return (
            None,
            min(2016, max(12, count)),
            {
                "price_provenance": "synthetic_fixed",
                "window_start": None,
                "window_end": None,
                "historical_price_sha256": None,
            },
        )
    if not isinstance(start_text, str) or not isinstance(end_text, str):
        raise ValueError("historical replay requires both window_start and window_end")
    try:
        start_date = datetime.fromisoformat(start_text).date()
        end_date = datetime.fromisoformat(end_text).date()
    except ValueError as exc:
        raise ValueError("historical replay windows must be ISO dates") from exc
    if start_date >= end_date:
        raise ValueError("historical replay window_end must be after window_start")
    available = int(
        (
            datetime.combine(end_date, time.min, tzinfo=UTC)
            - datetime.combine(start_date, time.min, tzinfo=UTC)
        ).total_seconds()
        // 300
    )
    count = available if requested_intervals is None else requested_intervals
    count = min(2016, max(12, count))
    if count > available:
        raise ValueError("interval_count exceeds the selected historical window")

    from eflux.agents.ppo.training_data import load_real_market_data

    data = load_real_market_data(start_date=start_date, end_date=end_date)
    if data.price is None or len(data.price) == 0:
        raise ValueError("the selected window has no platform-loaded historical price rows")
    start = datetime.combine(start_date, time.min, tzinfo=UTC)
    prices = [
        Decimal(str(data.price_at(start + timedelta(minutes=5 * index)))) for index in range(count)
    ]
    price_payload = {
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
        "prices": [str(value) for value in prices],
    }
    return (
        prices,
        count,
        {
            "price_provenance": "platform_loaded_caiso_history",
            "window_start": start_date.isoformat(),
            "window_end": end_date.isoformat(),
            "historical_price_sha256": content_sha256(price_payload),
            "historical_source_points": len(data.price),
        },
    )


def _episode_economics(sim, vpp, interval_count: int) -> dict[str, Any]:
    metrics = measure_episode(vpp.name, sim, vpp, interval_count)
    entries = [entry for entry in sim.gateway.ledger.entries if entry.participant_id == vpp.vpp_id]
    breakdown = sim.gateway.ledger.breakdown(vpp.vpp_id)
    running = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for entry in entries:
        running += entry.amount_usd
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
    trade_cash = float(breakdown.get(LedgerCategory.TRADE, Decimal("0")))
    degradation = max(0.0, -float(breakdown.get(LedgerCategory.BATTERY_DEGRADATION, Decimal("0"))))
    imbalance_cost = max(0.0, -float(breakdown.get(LedgerCategory.IMBALANCE, Decimal("0"))))
    fees = max(
        0.0,
        -float(breakdown.get(LedgerCategory.TRANSACTION_FEE, Decimal("0")))
        - float(breakdown.get(LedgerCategory.MESSAGE_FEE, Decimal("0"))),
    )
    llm_cost = sum(
        float((entry.get("usage_delta") or {}).get("estimated_cost_usd", 0.0))
        for entry in getattr(getattr(vpp.agent, "strategist", None), "replay_archive", ())
    )
    grid_fills = [
        trade
        for trade in vpp.recent_trades
        if int(trade.get("counterparty_vpp_id", 0)) == GRID_PARTICIPANT_ID
    ]
    peer_fills = [
        trade
        for trade in vpp.recent_trades
        if int(trade.get("counterparty_vpp_id", 0)) != GRID_PARTICIPANT_ID
    ]
    return {
        **asdict(metrics),
        "energy_traded_kwh": metrics.energy_traded_kwh,
        "gross_trade_pnl_usd": trade_cash,
        "net_pnl_usd": metrics.realized_pnl - llm_cost,
        "max_drawdown_usd": float(max_drawdown),
        "imbalance_cost_usd": imbalance_cost,
        "degradation_cost_usd": degradation,
        "transaction_and_message_fees_usd": fees,
        "llm_cost_usd": llm_cost,
        "gateway_rejection_rate": metrics.risk_rejections / max(1, interval_count),
        "data_missing_rate": 0.0,
        "grid_fill_count": len(grid_fills),
        "peer_fill_count": len(peer_fills),
        "grid_energy_kwh": sum(float(trade.get("qty", 0.0)) for trade in grid_fills),
        "peer_energy_kwh": sum(float(trade.get("qty", 0.0)) for trade in peer_fills),
    }


def _p2p_market_evidence(sim, candidate_vpp) -> dict[str, Any]:
    """Extract auditable market-wide dimensions from one closed-loop world.

    These are deliberately separate observations, not ingredients in a composite
    score.  Limit-price surplus is a transparent proxy based on submitted bids and
    asks; it is not claimed to be a complete utility model.
    """

    rows = list(getattr(sim, "_audit_buffer", ()))
    limit_prices: dict[int, tuple[str, float]] = {}
    order_submissions = 0
    for row in rows:
        payload = row.get("payload") or {}
        if row.get("kind") == "gateway.accepted":
            for order in payload.get("accepted_orders") or ():
                try:
                    limit_prices[int(order["order_id"])] = (
                        str(order["side"]),
                        float(order["price"]),
                    )
                    order_submissions += 1
                except (KeyError, TypeError, ValueError):
                    continue

    trades: list[dict[str, Any]] = []
    for row in rows:
        if row.get("kind") != "trade":
            continue
        payload = row.get("payload") or {}
        try:
            if GRID_PARTICIPANT_ID in {
                int(payload["buy_vpp_id"]),
                int(payload["sell_vpp_id"]),
            }:
                continue
            trades.append(payload)
        except (KeyError, TypeError, ValueError):
            continue

    volumes = [float(trade["qty"]) for trade in trades]
    prices = [float(trade["price"]) for trade in trades]
    surplus = 0.0
    surplus_trade_count = 0
    candidate_counterparties: set[int] = set()
    for trade, qty in zip(trades, volumes, strict=True):
        buy_id = int(trade["buy_vpp_id"])
        sell_id = int(trade["sell_vpp_id"])
        buy_limit = limit_prices.get(int(trade["buy_order_id"]))
        sell_limit = limit_prices.get(int(trade["sell_order_id"]))
        if buy_limit is not None and sell_limit is not None:
            surplus += max(0.0, buy_limit[1] - sell_limit[1]) * qty / 1000.0
            surplus_trade_count += 1
        if buy_id == candidate_vpp.vpp_id:
            candidate_counterparties.add(sell_id)
        elif sell_id == candidate_vpp.vpp_id:
            candidate_counterparties.add(buy_id)

    spreads: list[float] = []
    bid_depth = 0.0
    ask_depth = 0.0
    for interval in sim.engine.intervals:
        snapshot = sim.engine.snapshot(interval.interval_id, depth_levels=10**6)
        bids = [(float(price), float(qty)) for price, qty in snapshot["bids"]]
        asks = [(float(price), float(qty)) for price, qty in snapshot["asks"]]
        bid_depth += sum(qty for _, qty in bids)
        ask_depth += sum(qty for _, qty in asks)
        if bids and asks:
            spreads.append(max(0.0, asks[0][0] - bids[0][0]))

    volume = sum(volumes)
    vwap = (
        None if volume <= 0 else sum(p * q for p, q in zip(prices, volumes, strict=True)) / volume
    )
    price_stddev = pstdev(prices) if len(prices) > 1 else 0.0
    return {
        "peer_trade_count": len(trades),
        "peer_volume_kwh": volume,
        "peer_vwap_usd_per_mwh": vwap,
        "trade_price_stddev_usd_per_mwh": price_stddev,
        "end_book_mean_spread_usd_per_mwh": None if not spreads else fmean(spreads),
        "end_book_bid_depth_kwh": bid_depth,
        "end_book_ask_depth_kwh": ask_depth,
        "limit_price_surplus_proxy_usd": surplus,
        "surplus_observed_trade_count": surplus_trade_count,
        "accepted_order_count": order_submissions,
        "candidate_unique_peer_count": len(candidate_counterparties),
    }


def _mean_market_delta(reports: list[dict[str, Any]], key: str) -> float | None:
    deltas: list[float] = []
    for item in reports:
        for pair in item["report"]["pairs"]:
            treatment = pair.get("treatment_market_evidence") or {}
            control = pair.get("control_market_evidence") or {}
            if treatment.get(key) is None or control.get(key) is None:
                continue
            deltas.append(float(treatment[key]) - float(control[key]))
    return None if not deltas else fmean(deltas)


def _protocol_replay(
    release: dict[str, Any],
    config: dict[str, Any],
    *,
    market: str,
    llm_mode: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from eflux.ecosystem.runtime import agent_factory_from_release

    profile = _profile(config)
    params = VPPParams.from_dict(profile["spec"]["vpp_params"])
    requested_intervals = (
        int(config["interval_count"]) if config.get("interval_count") is not None else None
    )
    market_price_path, interval_count, price_context = _historical_grid_protocol(
        config, requested_intervals
    )
    if market not in {"realprice", "hybrid"} and market_price_path is not None:
        raise ValueError("historical grid prices are not a valid P2P replay protocol")
    seeds = tuple(int(seed) for seed in config.get("seeds", (11, 23, 37)))
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("evaluation seeds must be a non-empty unique list")

    rows = []
    for seed in seeds:
        require_archive_consumed = False
        if llm_mode is None:
            agent = agent_factory_from_release(release, learning=False)
        else:
            from eflux.ecosystem.llm_replay import (
                ArchivedTranscriptClient,
                archived_transcripts_for_seed,
                build_historical_llm_agent,
                platform_fresh_llm_client,
            )

            if llm_mode == "archived":
                client = ArchivedTranscriptClient(archived_transcripts_for_seed(release, seed))
                require_archive_consumed = True
            elif llm_mode == "fresh":
                client = platform_fresh_llm_client(release)
            else:
                raise ValueError(f"unsupported LLM replay mode {llm_mode!r}")
            agent = build_historical_llm_agent(release, client=client)
        completed = False
        try:
            sim, vpp = run_episode(
                lambda agent=agent: agent,
                n_ticks=interval_count,
                tick_h=5.0 / 60.0,
                episode_seed=seed,
                candidate_params=params,
                market_price_ref=Decimal(str(config.get("grid_price_usd_per_mwh", 50))),
                market_mode=market,
                market_price_path=market_price_path,
                market_price_source=(
                    "platform-loaded historical CAISO"
                    if market_price_path is not None
                    else "ecosystem fixed grid"
                ),
            )
            strategist = getattr(vpp.agent, "strategist", None)
            transcript = list(getattr(strategist, "replay_archive", ()))
            rows.append(
                {
                    "seed": seed,
                    **_episode_economics(sim, vpp, interval_count),
                    "llm_transcript": transcript,
                }
            )
            completed = True
        finally:
            close = getattr(agent, "close_historical_llm", None)
            if callable(close):
                close(require_archive_consumed=require_archive_consumed and completed)
    pnls = [row["net_pnl_usd"] for row in rows]
    throughput = [row["energy_traded_kwh"] for row in rows]
    battery_kwh = float(profile["spec"]["vpp_params"].get("battery_kwh", 0.0))
    mean_pnl = fmean(pnls)
    mean_throughput = fmean(throughput)
    metrics = {
        "gross_pnl_usd_mean": fmean(row["gross_trade_pnl_usd"] for row in rows),
        "net_pnl_usd_mean": mean_pnl,
        "net_pnl_usd_variance": fmean((value - mean_pnl) ** 2 for value in pnls),
        "net_pnl_usd_mean_ci95_approx": _normal_mean_ci95(pnls),
        "pnl_tail_p10_usd": _percentile(pnls, 0.10),
        "profit_per_battery_kwh_usd": None if battery_kwh <= 0 else mean_pnl / battery_kwh,
        "profit_per_mwh_throughput_usd": (
            None if mean_throughput <= 0 else mean_pnl / (mean_throughput / 1000.0)
        ),
        "max_drawdown_usd_mean": fmean(row["max_drawdown_usd"] for row in rows),
        "imbalance_cost_usd_mean": fmean(row["imbalance_cost_usd"] for row in rows),
        "degradation_cost_usd_mean": fmean(row["degradation_cost_usd"] for row in rows),
        "llm_cost_usd_mean": fmean(row["llm_cost_usd"] for row in rows),
        "gateway_rejection_rate_mean": fmean(row["gateway_rejection_rate"] for row in rows),
        "data_missing_rate_mean": 0.0,
        "llm_call_count_mean": fmean(len(row["llm_transcript"]) for row in rows),
        "llm_latency_ms_mean": (
            None
            if not any(row["llm_transcript"] for row in rows)
            else fmean(float(call["latency_ms"]) for row in rows for call in row["llm_transcript"])
        ),
        "seed_count": len(seeds),
    }
    evidence = {
        "protocol": (
            "eflux-simulator-v1-caiso-historical-grid"
            if market_price_path is not None
            else (
                "eflux-simulator-v1-fixed-exogenous-grid"
                if market == "realprice"
                else "eflux-simulator-v1-closed-loop"
            )
        ),
        "release_content_sha256": release["content_sha256"],
        "context": {
            "market": market,
            "profile": profile,
            "initial_soc_fraction": profile["spec"]["vpp_params"].get(
                "battery_initial_soc_frac", 0.0
            ),
            "interval_count": interval_count,
            "decision_interval_seconds": 300,
            "seeds": list(seeds),
            "grid_price_usd_per_mwh": config.get("grid_price_usd_per_mwh", 50),
            "data_version": (
                price_context["historical_price_sha256"]
                if market_price_path is not None
                else "fixed-protocol-v1"
            ),
            **price_context,
            "costs_included": {
                "transaction_fees": True,
                "imbalance": True,
                "battery_degradation": True,
                "llm": True,
            },
            "llm_replay_mode": llm_mode,
            "llm_calls_are_archived_and_hash_checked": llm_mode == "archived",
            "llm_network_access": llm_mode == "fresh",
        },
        "per_seed": rows,
    }
    return metrics, evidence


def _p2p_tournament(
    release: dict[str, Any], config: dict[str, Any], packs: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    from eflux.ecosystem.runtime import (
        agent_factory_from_release,
        bench_roster_from_population,
    )

    profile = _profile(config)
    params = VPPParams.from_dict(profile["spec"]["vpp_params"])
    interval_count = min(2016, max(12, int(config.get("interval_count", 288))))
    seeds = tuple(int(seed) for seed in config.get("seeds", (11, 23, 37)))
    reports = []
    for pack in packs:
        roster_seed = int(content_sha256({"pack": pack["id"]})[:8], 16)
        report = evaluate_paired_worlds(
            treatment_name=release["name"],
            treatment_factory=lambda: agent_factory_from_release(release, learning=False),
            control_name="same-asset-truthful-control",
            control_factory=lambda: TruthfulAgent(price_ref=Decimal("50")),
            seeds=seeds,
            interval_count=interval_count,
            candidate_params=params,
            market_price_ref=Decimal("50"),
            market_mode="p2p",
            counter_roster_factory=lambda pack=pack, roster_seed=roster_seed: (
                bench_roster_from_population(pack, roster_seed)
            ),
            market_evidence_factory=_p2p_market_evidence,
        )
        reports.append({"population": _population_evidence(pack), "report": report.to_dict()})
    uplifts = [
        pair["mark_to_market_uplift_usd"] for item in reports for pair in item["report"]["pairs"]
    ]
    imbalances = [
        pair["imbalance_reduction_kwh"] for item in reports for pair in item["report"]["pairs"]
    ]
    rejections = [
        pair["rejection_reduction"] for item in reports for pair in item["report"]["pairs"]
    ]
    metrics = {
        "paired_pnl_uplift_usd_mean": fmean(uplifts),
        "paired_pnl_uplift_usd_p10": _percentile(uplifts, 0.10),
        "paired_pnl_uplift_usd_mean_ci95_approx": _normal_mean_ci95(uplifts),
        "paired_pnl_uplift_usd_worst": min(uplifts),
        "treatment_win_rate": sum(value > 0 for value in uplifts) / len(uplifts),
        "population_robustness_stddev_usd": pstdev(uplifts),
        "exploitability_loss_proxy_usd": max(0.0, -min(uplifts)),
        "imbalance_reduction_kwh_mean": fmean(imbalances),
        "gateway_rejection_reduction_mean": fmean(rejections),
        "peer_volume_delta_kwh_mean": _mean_market_delta(reports, "peer_volume_kwh"),
        "end_book_spread_delta_usd_per_mwh_mean": _mean_market_delta(
            reports, "end_book_mean_spread_usd_per_mwh"
        ),
        "bid_depth_delta_kwh_mean": _mean_market_delta(reports, "end_book_bid_depth_kwh"),
        "ask_depth_delta_kwh_mean": _mean_market_delta(reports, "end_book_ask_depth_kwh"),
        "limit_price_surplus_proxy_delta_usd_mean": _mean_market_delta(
            reports, "limit_price_surplus_proxy_usd"
        ),
        "trade_price_volatility_delta_usd_per_mwh_mean": _mean_market_delta(
            reports, "trade_price_stddev_usd_per_mwh"
        ),
        "population_count": len(reports),
        "pair_count": len(uplifts),
    }
    return metrics, {
        "protocol": "paired-world-population-tournament-v1",
        "release_content_sha256": release["content_sha256"],
        "context": {
            "profile": profile,
            "seeds": list(seeds),
            "interval_count": interval_count,
            "candidate_isolated_from_control": True,
            "formal_default_includes_worker_hidden_rosters": any(
                item["population"].get("worker_hidden") for item in reports
            ),
        },
        "populations": reports,
        "metric_notes": {
            "exploitability_loss_proxy_usd": "Magnitude of the worst paired uplift loss; this is a conservative proxy, not a game-theoretic proof.",
            "market_impact": "Treatment-minus-control deltas expose peer volume, end-book spread/depth, price volatility, and a submitted-limit-price surplus proxy.",
            "decision_policy": "EFlux records dimensions and uncertainty without combining them into a universal score or declaring a better Agent.",
            "limit_price_surplus_proxy": "Sum of max(0, buy limit - sell limit) x kWh / 1000 for matched peer trades with both limits observed; not a complete utility model.",
            "confidence_intervals": "Approximate 95% normal intervals over independent seed/population pairs; inspect the full empirical distribution for small samples.",
        },
    }


def _hybrid_evaluation(
    release: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    hybrid_metrics, hybrid_evidence = _protocol_replay(release, config, market="hybrid")
    grid_metrics, grid_evidence = _protocol_replay(release, config, market="realprice")
    peer_config = {
        key: value for key, value in config.items() if key not in {"window_start", "window_end"}
    }
    peer_config["interval_count"] = hybrid_evidence["context"]["interval_count"]
    peer_metrics, peer_evidence = _protocol_replay(release, peer_config, market="p2p")
    hybrid_rows = hybrid_evidence["per_seed"]
    total_grid = sum(row["grid_energy_kwh"] for row in hybrid_rows)
    total_peer = sum(row["peer_energy_kwh"] for row in hybrid_rows)
    fill_count = sum(row["grid_fill_count"] + row["peer_fill_count"] for row in hybrid_rows)
    peer_fills = sum(row["peer_fill_count"] for row in hybrid_rows)
    hybrid_return = float(hybrid_metrics["net_pnl_usd_mean"])
    grid_return = float(grid_metrics["net_pnl_usd_mean"])
    peer_return = float(peer_metrics["net_pnl_usd_mean"])
    best_single = max(grid_return, peer_return)
    regret = max(0.0, best_single - hybrid_return)
    routing_quality = 1.0 - regret / max(1.0, abs(best_single))
    metrics = {
        **hybrid_metrics,
        "grid_only_baseline_return_usd": grid_return,
        "p2p_only_baseline_return_usd": peer_return,
        "p2p_incremental_uplift_usd": hybrid_return - grid_return,
        "routing_quality": max(0.0, min(1.0, routing_quality)),
        "grid_dependence": total_grid / max(1e-12, total_grid + total_peer),
        "peer_match_rate": peer_fills / max(1, fill_count),
        "final_imbalance_kwh": fmean(row["unresolved_imbalance_kwh"] for row in hybrid_rows),
    }
    return metrics, {
        "protocol": "hybrid-three-world-decomposition-v1",
        "release_content_sha256": release["content_sha256"],
        "hybrid": hybrid_evidence,
        "grid_only": grid_evidence,
        "p2p_only": peer_evidence,
        "routing_quality_formula": "1 - max(0, best(single-venue return) - hybrid return) / max(1, abs(best single-venue return))",
    }


def _pack_mapping(pack: PopulationPack) -> dict[str, Any]:
    return {
        "id": str(pack.spec.get("catalog_id") or pack.id),
        "version": pack.version,
        "name": pack.name,
        "description": pack.description,
        "market": "p2p",
        "spec": dict(pack.spec),
        "content_sha256": pack.content_sha256,
    }


async def _selected_packs(session: AsyncSession, config: dict[str, Any]) -> list[dict[str, Any]]:
    requested = config.get("population_pack_id")
    if requested is not None:
        pack = await session.get(PopulationPack, int(requested))
        if pack is None or pack.status not in ("published", "verified"):
            raise ValueError("population pack is unavailable")
        return [_pack_mapping(pack)]
    return [*list_builtin_population_packs(), *_hidden_population_packs()]


async def _live_evidence(
    session: AsyncSession,
    release: AgentRelease,
    config: dict[str, Any],
    *,
    deployment_mode: str = "live",
) -> tuple[dict[str, Any], dict[str, Any]]:
    managed_def_id = config.get("managed_def_id")
    if managed_def_id is None:
        raise ValueError("managed_def_id is required for deployment-bound evidence")
    conditions = (
        VppStatSnapshot.release_id == release.id,
        VppStatSnapshot.release_content_sha256 == release.content_sha256,
        VppStatSnapshot.managed_def_id == int(managed_def_id),
        VppStatSnapshot.owner_id == release.owner_id,
        VppStatSnapshot.deployment_mode == deployment_mode,
    )
    session_id = config.get("market_session_id")
    if session_id is None:
        session_id = (
            await session.execute(
                select(VppStatSnapshot.session_id)
                .where(*conditions)
                .order_by(VppStatSnapshot.wall_ts.desc(), VppStatSnapshot.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if session_id is None:
        raise ValueError(f"deployment has no hash-bound {deployment_mode} snapshots")
    query = select(VppStatSnapshot).where(
        *conditions,
        VppStatSnapshot.session_id == int(session_id),
    )
    snapshots = list(
        (
            await session.execute(query.order_by(VppStatSnapshot.sim_ts, VppStatSnapshot.id))
        ).scalars()
    )
    if len(snapshots) < 2:
        raise ValueError("release needs at least two hash-bound live snapshots")
    pnls = [float(row.pnl_usd) for row in snapshots]
    peak = pnls[0]
    max_drawdown = 0.0
    for value in pnls:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, peak - value)
    first, last = snapshots[0], snapshots[-1]
    metrics = {
        "net_pnl_usd": pnls[-1] - pnls[0],
        "max_drawdown_usd": max_drawdown,
        "imbalance_kwh": max(0.0, last.imbalance_kwh - first.imbalance_kwh),
        "degradation_cost_usd": max(0.0, last.degradation_cost_usd - first.degradation_cost_usd),
        "llm_cost_usd": max(0.0, last.llm_cost_usd - first.llm_cost_usd),
        "gateway_rejections": max(0, last.gateway_rejections - first.gateway_rejections),
        "fallback_count": max(0, last.fallback_count - first.fallback_count),
        "data_source_anomaly_seconds": None,
        "snapshot_count": len(snapshots),
    }
    evidence = {
        "protocol": "release-hash-bound-forward-observation-v1",
        "release_content_sha256": release.content_sha256,
        "runtime_start": first.sim_ts.isoformat(),
        "runtime_end": last.sim_ts.isoformat(),
        "release_version": release.version,
        "market_session_id": int(session_id),
        "owner_id": release.owner_id,
        "deployment_mode": deployment_mode,
        "snapshot_ids": [row.id for row in snapshots],
        "managed_definition_ids": sorted(
            {row.managed_def_id for row in snapshots if row.managed_def_id is not None}
        ),
    }
    return metrics, evidence


async def execute_release_evaluation(
    evaluation_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    factory = session_factory or get_sessionmaker()
    async with factory() as session:
        evaluation = await session.get(ReleaseEvaluation, evaluation_id)
        if evaluation is None or evaluation.status != "running":
            raise RuntimeError("release evaluation is not claimed")
        release = await session.get(AgentRelease, evaluation.release_id)
        if release is None or not release.content_sha256:
            raise RuntimeError("immutable release disappeared")
        release_payload = _release_payload(release)
        config = dict(evaluation.config or {})
        kind = evaluation.kind
        packs = await _selected_packs(session, config) if kind == "p2p_tournament" else []
        if kind in {"forward_shadow", "verified_live"}:
            try:
                metrics, evidence = await _live_evidence(
                    session,
                    release,
                    config,
                    deployment_mode="live" if kind == "verified_live" else "shadow",
                )
            except Exception as exc:
                evaluation.status = "failed"
                evaluation.error = f"{type(exc).__name__}: {exc}"[:4000]
                evaluation.claimed_at = None
                evaluation.lease_expires_at = None
                evaluation.finished_at = datetime.now(UTC)
                await session.commit()
                raise
        else:
            metrics = evidence = None

    try:
        if metrics is None or evidence is None:
            if kind == "p2p_tournament":
                metrics, evidence = await asyncio.to_thread(
                    _p2p_tournament, release_payload, config, packs
                )
            elif kind == "hybrid_evaluation":
                metrics, evidence = await asyncio.to_thread(
                    _hybrid_evaluation, release_payload, config
                )
            elif kind == "fresh_llm_replay":
                if not release_payload["recipe"].get("llm"):
                    raise ValueError("Fresh-LLM Replay requires a release with declared LLM config")
                if not config.get("window_start") or not config.get("window_end"):
                    raise ValueError(
                        "Fresh-LLM Historical Replay requires window_start and window_end"
                    )
                metrics, evidence = await asyncio.to_thread(
                    _protocol_replay,
                    release_payload,
                    config,
                    market=release_payload["market"],
                    llm_mode="fresh",
                )
            elif kind == "deterministic_replay" and release_payload["recipe"].get("llm"):
                metrics, evidence = await asyncio.to_thread(
                    _protocol_replay,
                    release_payload,
                    config,
                    market=release_payload["market"],
                    llm_mode="archived",
                )
            else:
                metrics, evidence = await asyncio.to_thread(
                    _protocol_replay,
                    release_payload,
                    config,
                    market=release_payload["market"],
                )
        evidence_sha256 = content_sha256(evidence)
        async with factory() as session:
            evaluation = await session.get(ReleaseEvaluation, evaluation_id)
            release = await session.get(AgentRelease, release_payload["id"])
            if evaluation is None or release is None:
                raise RuntimeError("evaluation disappeared before persistence")
            evaluation.status = "done"
            evaluation.metrics = metrics
            evaluation.evidence = evidence
            evaluation.evidence_sha256 = evidence_sha256
            evaluation.claimed_at = None
            evaluation.lease_expires_at = None
            evaluation.finished_at = datetime.now(UTC)
            badges = set(release.badges)
            if kind == "deterministic_replay":
                badges.add("Platform Backtested")
            elif kind == "fresh_llm_replay":
                badges.add("Fresh-LLM Replay")
            elif kind == "verified_live":
                badges.add("Verified Live")
            if release.environment.get("dependencies_locked") is True:
                badges.add("Reproducible")
            if release.recipe.get("online_learning") is True:
                badges.add("Online-Adaptive")
            release.badges = sorted(badges)
            release.status = "verified"
            await session.commit()
    except Exception as exc:
        async with factory() as session:
            await session.execute(
                update(ReleaseEvaluation)
                .where(ReleaseEvaluation.id == evaluation_id)
                .values(
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}"[:4000],
                    claimed_at=None,
                    lease_expires_at=None,
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()
        raise
