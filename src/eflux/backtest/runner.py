"""Headless historical backtest runner.

This module is intentionally backtest-only. It does not change the live simulator's
fallback behavior, scenario defaults, or wall-clock loop; it drives the existing
Simulator synchronously with explicit historical settings.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import signal
import sys
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml

from eflux.agents.base import MarketSnapshot
from eflux.agents.reflective.pool import validate_llm_connection
from eflux.bridge.bus import InMemoryBus
from eflux.config import PROJECT_ROOT, get_settings
from eflux.data.electricity_market import ExternalMarketQuote, synthetic_quote
from eflux.forecasting.service import ForecastService
from eflux.simulator.agent_spec import AgentSpec, validate_vpp_params
from eflux.simulator.runner import Simulator
from eflux.simulator.scenarios import load_default_scenario
from eflux.vpp.base import VPPParams

MarketMode = Literal["p2p", "realprice"]
LLMMode = Literal["live-strict"]

# Flat price ($/MWh) used only when a strict historical replay was requested but no real
# CAISO prices could be loaded (feed down / rate-limited). Surfaced explicitly in the
# manifest and logs so a degraded run is never silently presented as "historical".
_FALLBACK_PRICE = Decimal("50")
# Soft pre-flight guard: warn (never block) when the configuration implies at least this
# many live LLM calls. On a slow reasoning endpoint that can mean hours of wall-clock.
_LLM_CALL_WARN_THRESHOLD = 500

BACKTEST_SCENARIOS: dict[MarketMode, Path] = {
    "p2p": PROJECT_ROOT / "scenarios" / "p2p.yaml",
    "realprice": PROJECT_ROOT / "scenarios" / "realprice.yaml",
}

ENDOWMENT_FIELDS = (
    "pv_kw_peak",
    "battery_kwh",
    "battery_kw_max",
    "battery_eta_rt",
    "load_kw_base",
    "load_elasticity",
    "load_profile",
    "wind_kw_rated",
    "wind_mean_speed",
    "gas_kw_max",
    "gas_cost_per_mwh",
    "pv_lat",
    "pv_lon",
    "pv_tilt",
    "pv_azimuth",
)


class BacktestError(RuntimeError):
    """Backtest setup or execution failed."""


@dataclass(frozen=True)
class ScenarioInspection:
    path: Path
    declared_count: int
    hybrid_count: int
    mirror_count: int
    hybrid_names: tuple[str, ...]

    @property
    def live_count(self) -> int:
        return self.declared_count + self.mirror_count


@dataclass
class BacktestConfig:
    market_mode: MarketMode = "p2p"
    scenario: Path | None = None
    months: int = 1
    tick_seconds: float = 1.0
    llm_cadence_hours: float = 1.0
    llm_mode: LLMMode = "live-strict"
    out_dir: Path = PROJECT_ROOT / "artifacts" / "backtests"
    start: datetime | None = None
    end: datetime | None = None
    max_ticks: int | None = None
    sample_every_seconds: float = 3600.0
    train_ppo: bool = True
    ppo_episodes: int = 40
    ppo_epochs: int = 300
    fetch_real_data: bool = True
    validate_llm: bool = True
    # Strict mode requires a real LLM response (never fabricated guidance), but a single
    # transient blip — an empty completion from a reasoning model, a timeout, a 5xx —
    # should not throw away a multi-hour run. Retry a bounded number of times before
    # aborting. Set llm_max_attempts=1 to restore the original fail-fast behavior.
    llm_max_attempts: int = 3
    llm_retry_backoff_sec: float = 3.0


@dataclass(frozen=True)
class BacktestResult:
    run_dir: Path
    manifest_path: Path
    participant_metrics_path: Path
    timeseries_path: Path
    ticks_run: int
    llm_calls: int
    participant_count: int


def default_scenario_for_market(market_mode: MarketMode) -> Path:
    """Backtest defaults to the latest per-market rosters, never default.yaml."""
    try:
        return BACKTEST_SCENARIOS[market_mode]
    except KeyError as e:
        raise BacktestError(f"unsupported market mode: {market_mode!r}") from e


def resolve_scenario_path(market_mode: MarketMode, scenario: Path | str | None = None) -> Path:
    path = Path(scenario) if scenario is not None else default_scenario_for_market(market_mode)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def inspect_scenario(path: Path | str) -> ScenarioInspection:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entries = data.get("vpps") or []
    specs = [AgentSpec.model_validate(e) for e in entries]
    hybrids = tuple(s.name for s in specs if s.agent in ("hybrid", "reflective"))
    mirrors = sum(1 for s in specs if s.agent in ("hybrid", "reflective") and s.mirror)
    return ScenarioInspection(
        path=p,
        declared_count=len(specs),
        hybrid_count=len(hybrids),
        mirror_count=mirrors,
        hybrid_names=hybrids,
    )


def run_backtest(config: BacktestConfig | None = None) -> BacktestResult:
    """Run a backtest from synchronous callers such as the CLI."""
    return asyncio.run(_run_backtest(config or BacktestConfig()))


async def _run_backtest(config: BacktestConfig) -> BacktestResult:
    _validate_config(config)
    start, end = _window(config)
    scenario = resolve_scenario_path(config.market_mode, config.scenario)
    inspection = inspect_scenario(scenario)

    run_dir = _new_run_dir(config.out_dir, config.market_mode)
    run_dir.mkdir(parents=True, exist_ok=False)
    _log_progress(f"created backtest run directory: {run_dir}")

    checkpoint = None
    ppo_train_window = None
    scenario_for_run = scenario
    if config.train_ppo:
        _log_progress("training temporary PPO checkpoint for backtest window")
        checkpoint, train_start, train_end = _train_ppo_checkpoint(config, run_dir, start)
        ppo_train_window = {"start": train_start.isoformat(), "end": train_end.isoformat()}
        scenario_for_run = _write_scenario_with_checkpoint(scenario, checkpoint, run_dir)
        _log_progress(
            f"trained PPO checkpoint from {train_start.isoformat()} to {train_end.isoformat()}: {checkpoint}"
        )

    real_data = None
    real_price_points = 0
    if config.fetch_real_data:
        _log_progress(f"loading historical market data from {start.date()} to {end.date()}")
        real_data = _load_real_data(start.date(), end.date())
        real_price_points = _real_price_points(real_data)
        if real_price_points == 0:
            _log_progress(
                "WARNING: historical market data requested but no real prices were loaded "
                "(CAISO feed down or rate-limited). Falling back to a flat "
                f"{_FALLBACK_PRICE} $/MWh — this run is NOT a historical replay."
            )
        else:
            _log_progress(f"loaded {real_price_points} historical hourly prices")
    price_is_real = real_price_points > 0

    manifest = {
        "market_mode": config.market_mode,
        "scenario": str(scenario),
        "scenario_for_run": str(scenario_for_run),
        "declared_participants": inspection.declared_count,
        "expected_live_participants": inspection.live_count,
        "hybrid_llm_agents": list(inspection.hybrid_names),
        "months": config.months,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "tick_seconds": config.tick_seconds,
        "llm_mode": config.llm_mode,
        "llm_cadence_hours": config.llm_cadence_hours,
        "ppo_checkpoint": None if checkpoint is None else str(checkpoint),
        "ppo_train_window": ppo_train_window,
        "fetch_real_data": config.fetch_real_data,
        "train_ppo": config.train_ppo,
        "real_price_points": real_price_points,
        "price_source": "caiso_historical" if price_is_real else "synthetic_flat",
    }
    manifest_path = run_dir / "manifest.json"
    _write_json(manifest_path, manifest)

    with _backtest_env(config, scenario_for_run):
        settings = get_settings()
        # Capture the external-market quote parameters while the backtest env is active.
        # The tick loop runs after this context exits (env restored), so reading settings
        # there would pick up ambient shell values instead of the backtest's.
        quote_region = settings.market_region
        quote_node = settings.external_market_node
        quote_fee = Decimal(str(settings.external_market_transaction_fee))
        llm_enabled = bool(settings.reflective_enabled)
        if config.validate_llm and llm_enabled:
            _validate_live_strict_llm(settings)
        elif not llm_enabled:
            _log_progress("LLM strategist refresh disabled for backtest by EFLUX_REFLECTIVE_ENABLED=false")
        sim = Simulator(bus=InMemoryBus(), sim_epoch=start)
        sim.order_ttl_sec = max(config.tick_seconds * 180.0, config.tick_seconds)
        _log_progress(f"loading scenario for strict backtest: {scenario_for_run}")
        with _wall_clock_watchdog(
            _startup_llm_watchdog_sec(settings),
            "strict LLM startup/scenario validation",
        ):
            load_default_scenario(sim)
        forecast_warmup_window = _init_backtest_forecast_service(sim, start, settings)
        if llm_enabled:
            _require_strict_strategists(sim, expected=inspection.hybrid_count)
        _log_progress(f"loaded scenario with {len(sim.vpps)} live participants")

    ticks = _tick_count(start, end, config.tick_seconds)
    if config.max_ticks is not None:
        ticks = min(ticks, config.max_ticks)
    llm_every_ticks = max(1, round(config.llm_cadence_hours * 3600.0 / config.tick_seconds))
    sample_every_ticks = max(1, round(config.sample_every_seconds / config.tick_seconds))
    tick_h = config.tick_seconds / 3600.0
    step = timedelta(seconds=config.tick_seconds)

    # Pre-flight cost estimate: the live LLM fleet refreshes on `llm_every_ticks` (tick 0
    # included), and each refresh makes one strict call per hybrid agent. On a slow
    # reasoning endpoint this — not the tick loop — dominates wall-clock, so surface it
    # up front (and warn loudly) rather than letting an unbounded run wedge silently.
    expected_refreshes = len(range(0, ticks, llm_every_ticks)) if llm_enabled else 0
    expected_llm_calls = expected_refreshes * inspection.hybrid_count if llm_enabled else 0
    _log_progress(
        f"pre-flight estimate: {ticks} ticks, ~{expected_refreshes} LLM fleet refreshes, "
        f"~{expected_llm_calls} live LLM calls across {inspection.hybrid_count} agents"
    )
    if expected_llm_calls >= _LLM_CALL_WARN_THRESHOLD:
        _log_progress(
            f"WARNING: ~{expected_llm_calls} live LLM calls implied — this can take hours. "
            "Consider a shorter window, coarser --tick-seconds, larger --llm-cadence-hours, "
            "or --max-ticks."
        )
    manifest["expected_llm_calls"] = expected_llm_calls

    timeseries_rows: list[dict] = []
    aggregate_rows: list[dict] = []
    forecast_rows: list[dict] = []
    llm_calls = 0
    sim_ts = start
    tick_no = 0
    forecast_every_ticks = max(1, round(60.0 / config.tick_seconds))
    _log_progress(
        f"starting backtest loop: ticks={ticks}, tick_seconds={config.tick_seconds}, "
        f"llm_every_ticks={llm_every_ticks}"
    )
    participant_metrics_path = run_dir / "participant_metrics.csv"
    timeseries_path = run_dir / "timeseries.csv"
    forecast_timeseries_path = run_dir / "forecast_timeseries.csv"
    manifest.update(
        forecast_enabled=bool(settings.forecast_enabled),
        forecast_warmup_window=forecast_warmup_window,
        forecast_timeseries_path=str(forecast_timeseries_path),
        forecast_timeseries_points=0,
    )
    try:
        for tick_no in range(ticks):
            quote = _historical_quote(
                sim_ts, real_data, price_is_real, region=quote_region, node=quote_node, fee=quote_fee
            )
            sim._external_market_quote = quote
            sim._expire_orders(sim_ts)
            market = MarketSnapshot.from_engine(
                sim_ts,
                sim.engine.snapshot(depth_levels=5),
                external_market=quote,
                anchor_to_external=False,
                market_mode=config.market_mode,
            )
            market.recent_trades = sim._recent_market_trades()
            market.peer_reflections = sim._peer_reflections()
            if llm_enabled and tick_no % llm_every_ticks == 0:
                _log_progress(
                    f"refreshing strict LLM fleet at tick={tick_no} sim_ts={sim_ts.isoformat()}"
                )
                llm_calls += await _refresh_llm_fleet(
                    sim,
                    market,
                    max_attempts=config.llm_max_attempts,
                    retry_backoff_sec=config.llm_retry_backoff_sec,
                )

            if tick_no == 0 or tick_no % forecast_every_ticks == 0:
                _refresh_backtest_forecast(sim, sim_ts, quote, real_data)

            open_net = sim._open_orders_net_by_vpp()
            open_counts = sim._open_order_counts_by_vpp()
            for vpp in sim.vpps.values():
                sim._tick_vpp(
                    vpp,
                    sim_ts,
                    tick_h,
                    market,
                    open_orders_net_kwh=open_net.get(vpp.vpp_id, 0.0),
                    open_order_count=open_counts.get(vpp.vpp_id, 0),
                )
            if tick_no % sample_every_ticks == 0 or tick_no == ticks - 1:
                timeseries_rows.extend(_sample_rows(sim, sim_ts, tick_no))
                aggregate_rows.append(_sample_aggregate(sim, sim_ts, tick_no, quote.raw_lmp))
                row = _sample_forecast(sim, sim_ts, tick_no, quote.raw_lmp)
                if row is not None:
                    forecast_rows.append(row)
            if tick_no > 0 and tick_no % max(1, round(86400.0 / config.tick_seconds)) == 0:
                _log_progress(f"completed tick {tick_no} / {ticks}")
            sim_ts += step
    except Exception as e:
        # Salvage: even a hard strict-LLM abort should leave diagnosable artifacts (the
        # PnL accrued over the ticks that did run) rather than discarding the whole run.
        # Guard the salvage itself so a write failure can't mask the root cause.
        _log_progress(
            f"backtest aborted at tick {tick_no}: {type(e).__name__}: {e}; writing partial artifacts"
        )
        try:
            _write_artifacts(
                run_dir,
                sim,
                timeseries_rows,
                aggregate_rows,
                forecast_rows,
                participant_metrics_path,
                timeseries_path,
            )
            manifest.update(
                status="failed",
                error=f"{type(e).__name__}: {e}",
                ticks_run=tick_no,
                llm_calls=llm_calls,
                live_participants=len(sim.vpps),
                forecast_timeseries_points=len(forecast_rows),
                finished_at=datetime.now(UTC).isoformat(),
            )
            _write_json(manifest_path, manifest)
        except Exception as werr:
            _log_progress(f"failed to write partial artifacts: {type(werr).__name__}: {werr}")
        raise

    _log_progress("writing backtest artifacts")
    _write_artifacts(
        run_dir,
        sim,
        timeseries_rows,
        aggregate_rows,
        forecast_rows,
        participant_metrics_path,
        timeseries_path,
    )
    manifest.update(
        status="ok",
        ticks_run=ticks,
        llm_calls=llm_calls,
        live_participants=len(sim.vpps),
        forecast_timeseries_points=len(forecast_rows),
        finished_at=datetime.now(UTC).isoformat(),
    )
    _write_json(manifest_path, manifest)
    _log_progress(f"finished backtest run: {run_dir}")
    return BacktestResult(
        run_dir=run_dir,
        manifest_path=manifest_path,
        participant_metrics_path=participant_metrics_path,
        timeseries_path=timeseries_path,
        ticks_run=ticks,
        llm_calls=llm_calls,
        participant_count=len(sim.vpps),
    )


def _write_artifacts(
    run_dir: Path,
    sim: Simulator,
    timeseries_rows: list[dict],
    aggregate_rows: list[dict],
    forecast_rows: list[dict],
    participant_metrics_path: Path,
    timeseries_path: Path,
) -> None:
    participant_metrics = _participant_metrics(sim)
    group_metrics = _group_metrics(participant_metrics)
    _write_csv(participant_metrics_path, participant_metrics)
    _write_csv(timeseries_path, timeseries_rows)
    _write_csv(run_dir / "group_metrics.csv", group_metrics)
    _write_charts(run_dir, participant_metrics, timeseries_rows)
    _write_csv(run_dir / "forecast_timeseries.csv", forecast_rows)
    # Market-level series: the LMP the sim replayed, and aggregate supply/demand. These are
    # computed per tick but not otherwise persisted, so emit raw CSV + standalone charts.
    if aggregate_rows:
        _write_csv(run_dir / "market_timeseries.csv", aggregate_rows)
        _write_timeseries_svg(
            run_dir / "price_lmp.svg",
            "CAISO LMP replayed over eval window ($/MWh)",
            aggregate_rows,
            [("LMP $/MWh", "lmp", "#dc2626")],
        )
        _write_timeseries_svg(
            run_dir / "supply_demand.svg",
            "Total demand vs total renewable generation (kW)",
            aggregate_rows,
            [
                ("total load (demand)", "total_load_kw", "#dc2626"),
                ("total renewable (PV+wind)", "total_renew_kw", "#16a34a"),
            ],
        )
        _write_timeseries_svg(
            run_dir / "p2p_price.svg",
            "P2P peer clearing price vs CAISO grid reference ($/MWh)",
            aggregate_rows,
            [
                ("P2P last trade", "p2p_last_price", "#2563eb"),
                ("P2P mid", "p2p_mid", "#9333ea"),
                ("CAISO LMP", "lmp", "#dc2626"),
            ],
        )


class _CutoffRealMarketData:
    """Prefix view over RealMarketData so forecast refresh never sees future realized weather."""

    def __init__(self, data, cutoff: datetime) -> None:
        self.data = data
        self.cutoff = cutoff

    def set_cutoff(self, cutoff: datetime) -> None:
        self.cutoff = cutoff

    def _allowed(self, ts: datetime) -> bool:
        return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0) <= self.cutoff

    def ghi_at(self, ts: datetime, default: float = 0.0) -> float:
        if self.data is None or not self._allowed(ts):
            return default
        return self.data.ghi_at(ts, default)

    def wind_speed_at(self, ts: datetime, default: float = 0.0) -> float:
        if self.data is None or not self._allowed(ts):
            return default
        return self.data.wind_speed_at(ts, default)

    def _weather_field(self, df, col: str, ts: datetime, default: float) -> float:
        if self.data is None or not self._allowed(ts):
            return default
        return self.data._weather_field(df, col, ts, default)

    @property
    def weather(self):
        return None if self.data is None else self.data.weather

    @property
    def wind(self):
        return None if self.data is None else self.data.wind


def _init_backtest_forecast_service(sim: Simulator, start: datetime, settings) -> dict:
    """Create the shared forecast service for synchronous backtests.

    Warmup is intentionally bounded to samples strictly before the eval start. The raw
    loader may return an inclusive weather day, so filter the series before handing it to
    the reusable service.
    """
    if not settings.forecast_enabled:
        sim.forecast_service = None
        return {"enabled": False, "status": "disabled"}

    warmup_days = max(1, int(settings.forecast_warmup_days))
    warmup_start = (start - timedelta(days=warmup_days)).date()
    warmup_end = start.date()
    warmup_cutoff = start - timedelta(microseconds=1)
    try:
        warmup_data = _load_real_data(warmup_start, warmup_end)
        sim._forecast_real_data = _CutoffRealMarketData(warmup_data, warmup_cutoff)
        nwp = sim._forecast_nwp_lookups()
        service = ForecastService(nwp=nwp)
        series = _forecast_series_before(sim._forecast_warmup_series(warmup_data), start)
        service.warm_start(series=series, nwp=nwp)
        sim.forecast_service = service
        counts = {name: len(samples) for name, samples in series.items()}
        _log_progress(
            "forecast service warm-started leakage-free from "
            f"{warmup_start.isoformat()} to {warmup_end.isoformat()} exclusive"
        )
        return {
            "enabled": True,
            "status": "warm_started",
            "start": warmup_start.isoformat(),
            "end_exclusive": warmup_end.isoformat(),
            "sample_counts": counts,
        }
    except Exception as e:
        _log_progress(
            "WARNING: forecast warm-start skipped; continuing with empty online models: "
            f"{type(e).__name__}: {e}"
        )
        try:
            sim.forecast_service = ForecastService(nwp=sim._forecast_nwp_lookups())
        except Exception:
            sim.forecast_service = ForecastService()
        return {
            "enabled": True,
            "status": "empty_after_warmup_failure",
            "start": warmup_start.isoformat(),
            "end_exclusive": warmup_end.isoformat(),
            "error": f"{type(e).__name__}: {e}",
        }


def _forecast_series_before(
    series: dict[str, list[tuple[datetime, float]]],
    cutoff: datetime,
) -> dict[str, list[tuple[datetime, float]]]:
    return {
        name: [(ts, value) for ts, value in samples if ts < cutoff]
        for name, samples in series.items()
    }


def _refresh_backtest_forecast(
    sim: Simulator,
    sim_ts: datetime,
    quote: ExternalMarketQuote,
    real_data,
) -> None:
    service = getattr(sim, "forecast_service", None)
    if service is None:
        return
    data_view = getattr(sim, "_forecast_real_data", None)
    if isinstance(data_view, _CutoffRealMarketData):
        if data_view.data is not real_data:
            data_view.data = real_data
        data_view.set_cutoff(sim_ts)
    elif real_data is not None:
        sim._forecast_real_data = _CutoffRealMarketData(real_data, sim_ts)
    try:
        service.observe(
            sim_ts,
            price_real=quote.raw_lmp if quote.is_real_price else None,
            price_p2p=getattr(sim.engine, "last_price", None),
            ghi=sim._forecast_weather_value("ghi", sim_ts, 0.0),
            temp_air=sim._forecast_weather_value("temp_air", sim_ts, 20.0),
            wind_speed=sim._forecast_weather_value("wind_speed", sim_ts, 0.0),
        )
        service.refresh(sim_ts)
    except Exception as e:
        _log_progress(
            "WARNING: forecast observe/refresh failed; continuing with previous bundle: "
            f"{type(e).__name__}: {e}"
        )


def _sample_forecast(sim: Simulator, sim_ts: datetime, tick_no: int, lmp) -> dict | None:
    service = getattr(sim, "forecast_service", None)
    if service is None:
        return None
    forecast = service.latest
    last_price, _, _ = _engine_prices(sim)
    row = {
        "tick": tick_no,
        "sim_ts": sim_ts.isoformat(),
        "forecast_as_of": forecast.as_of.isoformat(),
        "realized_lmp": float(lmp),
        "realized_p2p": last_price,
    }
    for target_name in ("price_real", "price_p2p", "ghi"):
        target = getattr(forecast, target_name)
        for horizon in ("5m", "1h", "12h"):
            row[f"forecast_{target_name}_{horizon}"] = round(
                float(target.by_horizon(horizon).value), 6
            )
    return row


def _sample_aggregate(sim: Simulator, sim_ts: datetime, tick_no: int, lmp) -> dict:
    """Market-wide aggregates at one sample tick: the replayed grid LMP, the *peer* book
    prices (the actual P2P price discovery), and summed DER state (total demand vs total
    renewable generation) across every live participant. Sampled after the tick's matching,
    so the peer prices reflect trades up to and including this tick."""
    total_load = total_pv = total_wind = total_net = 0.0
    for vpp in sim.vpps.values():
        total_load += float(vpp.state.load_kw)
        total_pv += float(vpp.state.pv_kw)
        total_wind += float(vpp.state.wind_kw)
        total_net += float(vpp.state.net_kw)
    last_price, best_bid, best_ask = _engine_prices(sim)
    mid = (best_bid + best_ask) / 2.0 if best_bid is not None and best_ask is not None else None
    return {
        "tick": tick_no,
        "sim_ts": sim_ts.isoformat(),
        # CAISO grid LMP: a reference in p2p, the settlement price in realprice.
        "lmp": float(lmp),
        # Peer-book price discovery. None until the book first prints / when there is no
        # peer book (e.g. realprice, which settles against the grid quote, not peers).
        "p2p_last_price": last_price,
        "p2p_best_bid": best_bid,
        "p2p_best_ask": best_ask,
        "p2p_mid": None if mid is None else round(mid, 4),
        "total_load_kw": round(total_load, 4),
        "total_pv_kw": round(total_pv, 4),
        "total_wind_kw": round(total_wind, 4),
        "total_renew_kw": round(total_pv + total_wind, 4),
        "total_net_kw": round(total_net, 4),
    }


def _engine_prices(sim: Simulator) -> tuple[float | None, float | None, float | None]:
    """Current peer-book prices from the matching engine: last executed trade price and
    best bid/ask. None when the book has not printed or holds no resting orders — read
    defensively so a mode without a peer book (realprice) simply yields blanks."""
    engine = getattr(sim, "engine", None)
    if engine is None:
        return None, None, None
    book = getattr(engine, "book", None)
    bb = book.best_bid() if book is not None else None
    ba = book.best_ask() if book is not None else None

    def _f(x) -> float | None:
        return None if x is None else float(x)

    return (
        _f(getattr(engine, "last_price", None)),
        _f(bb.price if bb is not None else None),
        _f(ba.price if ba is not None else None),
    )


def _write_timeseries_svg(
    path: Path, title: str, rows: list[dict], series: list[tuple[str, str, str]]
) -> None:
    """Minimal multi-series line chart over the sampled ticks. `series` is a list of
    (legend label, row key, hex color)."""
    width, height = 1200, 480
    left, top, plot_w, plot_h = 80, 50, 1020, 360
    if not rows:
        path.write_text(_svg_header(width, height) + "</svg>", encoding="utf-8")
        return
    xs = [float(r["tick"]) for r in rows]
    x0, x1 = min(xs), max(xs)
    span_x = (x1 - x0) or 1.0
    vals = [float(r[k]) for (_, k, _) in series for r in rows if r.get(k) is not None]
    padded_vals = [*vals, 0.0]
    lo, hi = min(padded_vals), max(padded_vals)
    if lo == hi:
        lo -= 1.0
        hi += 1.0
    span_y = hi - lo
    parts = [
        _svg_header(width, height),
        f'<text x="24" y="30" font-size="18">{_xml(title)}</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fff" stroke="#cbd5e1" />',
    ]
    y_zero = top + (hi - 0.0) / span_y * plot_h
    if top <= y_zero <= top + plot_h:
        parts.append(
            f'<line x1="{left}" y1="{y_zero:.1f}" x2="{left + plot_w}" y2="{y_zero:.1f}" '
            'stroke="#94a3b8" stroke-dasharray="4 4" />'
        )
    for i, (label, key, color) in enumerate(series):
        pts = []
        for r in rows:
            if r.get(key) is None:
                continue
            x = left + (float(r["tick"]) - x0) / span_x * plot_w
            y = top + (hi - float(r[key])) / span_y * plot_h
            pts.append(f"{x:.1f},{y:.1f}")
        parts.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{" ".join(pts)}" />'
        )
        ly = top + 18 + i * 18
        parts.append(
            f'<line x1="{left + plot_w - 230}" y1="{ly - 4}" x2="{left + plot_w - 205}" '
            f'y2="{ly - 4}" stroke="{color}" stroke-width="3" />'
        )
        parts.append(f'<text x="{left + plot_w - 198}" y="{ly}" font-size="12">{_xml(label)}</text>')
    parts.append(
        f'<text x="{left}" y="{height - 14}" font-size="11">'
        f'ticks {int(x0)}..{int(x1)} (hourly samples) | y range {lo:.1f}..{hi:.1f}</text>'
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _validate_config(config: BacktestConfig) -> None:
    if config.months < 1:
        raise BacktestError("months must be >= 1")
    if config.tick_seconds <= 0:
        raise BacktestError("tick_seconds must be > 0")
    if config.llm_cadence_hours <= 0:
        raise BacktestError("llm_cadence_hours must be > 0")
    if config.llm_mode != "live-strict":
        raise BacktestError("backtest currently supports only llm_mode='live-strict'")


def _window(config: BacktestConfig) -> tuple[datetime, datetime]:
    if config.start is not None and config.end is not None:
        start = _utc_hour(config.start)
        end = _utc_hour(config.end)
    elif config.start is not None:
        start = _utc_hour(config.start)
        end = start + timedelta(days=30 * config.months)
    elif config.end is not None:
        end = _utc_hour(config.end)
        start = end - timedelta(days=30 * config.months)
    else:
        end_d = date.today() - timedelta(days=1)
        end = datetime(end_d.year, end_d.month, end_d.day, tzinfo=UTC)
        start = end - timedelta(days=30 * config.months)
    if start >= end:
        raise BacktestError("start must be before end")
    return start, end


def _utc_hour(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _tick_count(start: datetime, end: datetime, tick_seconds: float) -> int:
    return max(0, int((end - start).total_seconds() // tick_seconds))


def _new_run_dir(out_dir: Path, market_mode: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return out_dir / f"{stamp}-{market_mode}"


def _load_real_data(start_d: date, end_d: date):
    from eflux.agents.ppo.training_data import load_real_market_data

    return load_real_market_data(start_date=start_d, end_date=end_d)


def _real_price_points(real_data) -> int:
    """Number of real historical price rows actually loaded (0 = nothing usable).

    A degraded fetch (CAISO unreachable / rate-limited) returns an empty price series; the
    runner uses this to decide whether the run is a true historical replay or a flat
    synthetic fallback, and labels artifacts honestly either way."""
    if real_data is None:
        return 0
    price = getattr(real_data, "price", None)
    try:
        return len(price) if price is not None else 0
    except TypeError:
        return 0


def _train_ppo_checkpoint(config: BacktestConfig, run_dir: Path, start: datetime) -> tuple[Path, date, date]:
    from eflux.agents.ppo.train import run_training

    train_end = start.date()
    train_start = train_end - timedelta(days=30)
    out = run_dir / f"bc_primitive_{config.market_mode}_backtest.pt"
    run_training(
        str(out),
        real_data=True,
        days=30,
        episodes=config.ppo_episodes,
        epochs=config.ppo_epochs,
        market_mode=config.market_mode,
        start_date=train_start,
        end_date=train_end,
    )
    return out, train_start, train_end


def _write_scenario_with_checkpoint(source: Path, checkpoint: Path, run_dir: Path) -> Path:
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    for entry in data.get("vpps") or []:
        executor = entry.get("executor")
        if isinstance(executor, dict) and executor.get("kind") == "ppo_online":
            executor["checkpoint"] = str(checkpoint)
    out = run_dir / f"{source.stem}_backtest.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


@contextmanager
def _backtest_env(config: BacktestConfig, scenario: Path) -> Iterator[None]:
    reflective_enabled = os.environ.get("EFLUX_REFLECTIVE_ENABLED", "true")
    updates = {
        "EFLUX_MARKET_MODE": config.market_mode,
        "EFLUX_SCENARIO_FILE": str(scenario),
        "EFLUX_REFLECTIVE_ENABLED": reflective_enabled,
        "EFLUX_MARKET_TICK_SEC": str(config.tick_seconds),
        # Backtest owns strict LLM cadence explicitly via _refresh_llm_fleet().
        # Keep HybridPolicyAgent's live async cadence inert so hourly means hourly.
        "EFLUX_REFLECTIVE_INTERVAL_TICKS": "1000000000",
        # The backtest runner owns historical timing explicitly. Do not let the
        # live scenario loader prefetch wall-clock weather for historical dates.
        "EFLUX_PV_PHYSICAL": "false",
    }
    old = {k: os.environ.get(k) for k in updates}
    try:
        os.environ.update(updates)
        get_settings.cache_clear()
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def _validate_live_strict_llm(settings) -> None:
    missing = []
    if not settings.llm_api_key:
        missing.append(settings.llm_key_file)
    if not settings.llm_base_url:
        missing.append("EFLUX_LLM_BASE_URL")
    if not settings.llm_model:
        missing.append("EFLUX_LLM_MODEL")
    if missing:
        raise BacktestError("strict LLM backtest missing: " + ", ".join(missing))
    _log_progress(
        f"validating strict LLM connection: {settings.llm_provider}:{settings.llm_model}"
    )
    with _wall_clock_watchdog(
        _startup_llm_watchdog_sec(settings),
        "strict LLM startup connection validation",
    ):
        ok, detail = validate_llm_connection(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )
    if not ok:
        raise BacktestError(f"strict LLM connection validation failed: {detail}")
    _log_progress("strict LLM connection validated")


def _require_strict_strategists(sim: Simulator, *, expected: int) -> None:
    managed = sim.my_managed_vpps()
    if len(managed) != expected:
        raise BacktestError(f"expected {expected} LLM agents, loaded {len(managed)}")
    missing = [v.name for v in managed if getattr(v.agent, "strategist", None) is None]
    if missing:
        raise BacktestError(
            "strict LLM backtest requires live strategists; missing for " + ", ".join(missing)
        )
    for vpp in managed:
        vpp.agent.strategist.raise_errors = True


def _historical_quote(
    sim_ts: datetime,
    real_data,
    price_is_real: bool,
    *,
    region: str,
    node: str,
    fee: Decimal,
) -> ExternalMarketQuote:
    """Build the external-market quote for a tick. Pure: all settings are captured by the
    caller under the backtest env, so the per-tick path never reads ambient settings.

    When real CAISO prices are available the quote replays them and is labeled historical;
    when they are not, it is a clearly-labeled flat synthetic fallback (never passed off as
    a historical replay)."""
    if price_is_real:
        price = Decimal(str(real_data.price_at(sim_ts)))
        status, source, detail = (
            "real",
            "Backtest historical CAISO",
            "Backtest historical LMP replay.",
        )
    else:
        price = _FALLBACK_PRICE
        status, source, detail = (
            "synthetic",
            "Backtest synthetic fallback",
            f"No real CAISO prices available; flat {_FALLBACK_PRICE} $/MWh (not historical).",
        )
    quote = synthetic_quote(
        region=region,
        node=node,
        price=price,
        status=status,
        source=source,
        detail=detail,
        now=sim_ts,
        transaction_fee=fee,
    )
    hour = sim_ts.replace(minute=0, second=0, microsecond=0)
    return replace(
        quote,
        interval_start=hour,
        interval_end=hour + timedelta(hours=1),
    )


async def _refresh_llm_fleet(
    sim: Simulator,
    market: MarketSnapshot,
    *,
    max_attempts: int = 1,
    retry_backoff_sec: float = 0.0,
) -> int:
    """Refresh every managed agent's strategist with a real LLM call.

    Strict: a refresh must produce a real LLM response (guidance is never fabricated). But
    a single transient blip — an empty completion from a reasoning model, a timeout, a 5xx
    — is retried up to `max_attempts` times before the run is aborted, so one flaky call
    can't throw away a multi-hour backtest. Exhausting the retries still raises."""
    attempts = max(1, max_attempts)
    calls = 0
    for vpp in sim.my_managed_vpps():
        strategist = getattr(vpp.agent, "strategist", None)
        if strategist is None:
            raise BacktestError(f"{vpp.name} has no live strategist in strict LLM backtest")
        grid = market.external_market
        watchdog_sec = _strategist_watchdog_sec(strategist)
        _log_progress(
            f"strict LLM call start: agent={vpp.name} sim_ts={market.sim_ts.isoformat()} "
            f"watchdog={watchdog_sec:.0f}s"
        )
        for attempt in range(1, attempts + 1):
            try:
                with _wall_clock_watchdog(
                    watchdog_sec,
                    f"strict LLM refresh for {vpp.name} at {market.sim_ts.isoformat()}",
                ):
                    await asyncio.wait_for(
                        strategist.arefresh(
                            recent_pnl=[float(vpp.state.pnl)],
                            soc_frac=vpp.battery.soc_frac,
                            best_bid=float(market.best_bid) if market.best_bid is not None else None,
                            best_ask=float(market.best_ask) if market.best_ask is not None else None,
                            last_price=float(market.last_price) if market.last_price is not None else None,
                            market_mode=market.market_mode,
                            grid_raw_lmp=float(grid.raw_lmp) if grid is not None else None,
                            grid_import_price=float(grid.import_price) if grid is not None else None,
                            grid_export_price=float(grid.export_price) if grid is not None else None,
                            grid_status=grid.status if grid is not None else None,
                        ),
                        timeout=watchdog_sec,
                    )
                break
            except Exception as e:
                if attempt < attempts:
                    _log_progress(
                        f"strict LLM call retry {attempt}/{attempts - 1} for {vpp.name} "
                        f"at {market.sim_ts.isoformat()}: {type(e).__name__}: {e}"
                    )
                    if retry_backoff_sec > 0:
                        await asyncio.sleep(retry_backoff_sec)
                    continue
                raise BacktestError(
                    f"strict LLM refresh failed for {vpp.name} at {market.sim_ts.isoformat()} "
                    f"after {attempts} attempt(s): {type(e).__name__}: {e}"
                ) from e
        calls += 1
        _log_progress(f"strict LLM call ok: agent={vpp.name} sim_ts={market.sim_ts.isoformat()}")
    return calls


def _startup_llm_watchdog_sec(settings) -> float:
    return max(30.0, min(float(settings.llm_timeout_sec), 60.0))


def _strategist_watchdog_sec(strategist) -> float:
    try:
        hard = float(getattr(strategist, "hard_timeout_sec", 180.0))
    except (TypeError, ValueError):
        hard = 180.0
    return max(30.0, hard + 30.0)


@contextmanager
def _wall_clock_watchdog(timeout_sec: float, label: str) -> Iterator[None]:
    """Backtest-only process alarm for strict LLM calls that wedge below asyncio/httpx."""

    if (
        timeout_sec <= 0
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def _raise_timeout(signum, frame):
        del signum, frame
        raise BacktestError(f"{label} exceeded {timeout_sec:.1f}s")

    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    old_timer = signal.setitimer(signal.ITIMER_REAL, timeout_sec)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])


def _log_progress(message: str) -> None:
    print(f"[backtest {datetime.now(UTC).isoformat()}] {message}", file=sys.stderr, flush=True)


def _sample_rows(sim: Simulator, sim_ts: datetime, tick_no: int) -> list[dict]:
    rows = []
    for vpp in sim.vpps.values():
        rows.append(
            {
                "tick": tick_no,
                "sim_ts": sim_ts.isoformat(),
                "vpp_id": vpp.vpp_id,
                "name": vpp.name,
                "group_id": _group_id(vpp.params),
                "pnl": float(vpp.state.pnl),
                "pending_net_kwh": vpp.state.pending_net_kwh,
                "soc_frac": vpp.battery.soc_frac,
                "energy_bought_kwh": vpp.state.cumulative_energy_bought_kwh,
                "energy_sold_kwh": vpp.state.cumulative_energy_sold_kwh,
                "trade_count": vpp.trade_count,
            }
        )
    return rows


def _participant_metrics(sim: Simulator) -> list[dict]:
    rows = []
    last = float(sim.engine.last_price) if sim.engine.last_price is not None else 0.0
    open_net = sim._open_orders_net_by_vpp()
    for vpp in sim.vpps.values():
        pending = vpp.state.pending_net_kwh
        mark_to_market = float(vpp.state.pnl) + (pending + vpp.battery.soc_kwh) * last
        rows.append(
            {
                "vpp_id": vpp.vpp_id,
                "name": vpp.name,
                "strategy": vpp.strategy,
                "is_llm": vpp.is_my_vpp,
                "mirror_of": vpp.mirror_of or "",
                "group_id": _group_id(vpp.params),
                "realized_pnl": float(vpp.state.pnl),
                "mark_to_market": mark_to_market,
                "energy_bought_kwh": vpp.state.cumulative_energy_bought_kwh,
                "energy_sold_kwh": vpp.state.cumulative_energy_sold_kwh,
                "trade_count": vpp.trade_count,
                "risk_rejections": sim.risk_rejections_by_vpp.get(vpp.vpp_id, 0),
                "unresolved_imbalance_kwh": abs(pending + open_net.get(vpp.vpp_id, 0.0)),
                "final_soc_frac": vpp.battery.soc_frac,
            }
        )
    return sorted(rows, key=lambda r: float(r["mark_to_market"]), reverse=True)


def _group_metrics(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["group_id"])].append(row)
    out = []
    for group_id, items in sorted(grouped.items()):
        out.append(
            {
                "group_id": group_id,
                "participants": len(items),
                "names": ";".join(str(i["name"]) for i in items),
                "best_mark_to_market": max(float(i["mark_to_market"]) for i in items),
                "worst_mark_to_market": min(float(i["mark_to_market"]) for i in items),
                "total_energy_kwh": sum(
                    float(i["energy_bought_kwh"]) + float(i["energy_sold_kwh"]) for i in items
                ),
                "total_rejections": sum(int(i["risk_rejections"]) for i in items),
            }
        )
    return out


def _group_id(params: VPPParams) -> str:
    normalized = validate_vpp_params(params.to_dict())
    payload = {k: normalized[k] for k in ENDOWMENT_FIELDS}
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _write_charts(run_dir: Path, metrics: list[dict], timeseries: list[dict]) -> None:
    _write_bar_svg(run_dir / "overview_leaderboard.svg", metrics)
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in timeseries:
        by_group[str(row["group_id"])].append(row)
    for group_id, rows in by_group.items():
        names = {str(r["name"]) for r in rows}
        if len(names) < 2:
            continue
        _write_line_svg(run_dir / f"group_{group_id}_pnl.svg", rows)


def _write_bar_svg(path: Path, rows: list[dict]) -> None:
    width = 1200
    bar_h = 18
    gap = 6
    rows = rows[:60]
    height = max(120, 60 + len(rows) * (bar_h + gap))
    values = [float(r["mark_to_market"]) for r in rows] or [0.0]
    lo, hi = min(0.0, min(values)), max(0.0, max(values))
    span = hi - lo or 1.0
    zero = 260 + (0.0 - lo) / span * 850
    parts = [_svg_header(width, height), '<text x="24" y="30" font-size="18">Backtest MTM leaderboard</text>']
    for i, row in enumerate(rows):
        y = 54 + i * (bar_h + gap)
        value = float(row["mark_to_market"])
        x = 260 + (min(value, 0.0) - lo) / span * 850
        w = abs(value) / span * 850
        fill = "#2563eb" if value >= 0 else "#dc2626"
        parts.append(f'<text x="24" y="{y + 14}" font-size="12">{_xml(str(row["name"])[:30])}</text>')
        parts.append(f'<rect x="{x:.2f}" y="{y}" width="{max(w, 1):.2f}" height="{bar_h}" fill="{fill}" />')
        parts.append(f'<text x="1120" y="{y + 14}" font-size="12" text-anchor="end">{value:.2f}</text>')
    parts.append(f'<line x1="{zero:.2f}" y1="45" x2="{zero:.2f}" y2="{height - 20}" stroke="#334155" />')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_line_svg(path: Path, rows: list[dict]) -> None:
    width, height = 1200, 500
    left, top, plot_w, plot_h = 70, 40, 1040, 390
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_name[str(row["name"])].append(row)
    ticks = sorted({int(r["tick"]) for r in rows})
    values = [float(r["pnl"]) for r in rows] or [0.0]
    lo, hi = min(values), max(values)
    if lo == hi:
        lo -= 1.0
        hi += 1.0
    t0, t1 = (ticks[0], ticks[-1]) if ticks else (0, 1)
    span_t = max(1, t1 - t0)
    colors = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#f59e0b", "#0891b2", "#475569"]
    parts = [_svg_header(width, height), f'<text x="24" y="24" font-size="18">Group {path.stem} PnL</text>']
    parts.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fff" stroke="#cbd5e1" />')
    for idx, (name, series) in enumerate(sorted(by_name.items())):
        pts = []
        for row in sorted(series, key=lambda r: int(r["tick"])):
            x = left + (int(row["tick"]) - t0) / span_t * plot_w
            y = top + (hi - float(row["pnl"])) / (hi - lo) * plot_h
            pts.append(f"{x:.2f},{y:.2f}")
        color = colors[idx % len(colors)]
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{" ".join(pts)}" />')
        ly = top + 18 + idx * 18
        parts.append(f'<line x1="1120" y1="{ly - 5}" x2="1145" y2="{ly - 5}" stroke="{color}" stroke-width="2" />')
        parts.append(f'<text x="1150" y="{ly}" font-size="11">{_xml(name[:18])}</text>')
    parts.append(f'<text x="{left}" y="{height - 28}" font-size="12">tick {t0} to {t1}</text>')
    parts.append(f'<text x="{left}" y="{top + plot_h + 18}" font-size="12">PnL range {lo:.2f} to {hi:.2f}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _svg_header(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Arial, sans-serif" fill="#0f172a">'
    )


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
