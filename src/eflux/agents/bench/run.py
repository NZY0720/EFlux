"""Headless benchmark runner.

Drives the matching engine synchronously (no async clock) so a full sim-day runs in
milliseconds and is perfectly reproducible. Each candidate gets a fresh Simulator with
the same fixed sim epoch, counter-roster, and test slot. Run via `./tasks.sh bench`.
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from eflux.agents.base import MarketSnapshot
from eflux.agents.bench.metrics import EpisodeMetrics, format_leaderboard
from eflux.agents.bench.scenarios import candidates, counter_roster, test_slot_params
from eflux.bridge.bus import InMemoryBus
from eflux.forecasting.service import ForecastService
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams

# Fixed sim epoch so PV/load time-of-day profiles are identical across runs.
BENCH_EPOCH = datetime(2024, 6, 1, 0, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))


class CandidateEpisodeError(RuntimeError):
    """An exception attributable to the candidate rather than benchmark infrastructure."""


def _bench_ghi(ts: datetime) -> float:
    """Clear-sky diurnal GHI, mirroring the PPO training env's synthetic branch."""
    hour = ts.hour + ts.minute / 60.0
    sun = math.sin(math.pi * (hour - 6) / 12) if 6 <= hour <= 18 else 0.0
    return max(0.0, 1000.0 * sun)


def _observe_and_refresh_forecast(sim: Simulator, sim_ts: datetime) -> None:
    """Mirror the live refresh loop inside the episode so agents get real (warm)
    forecast channels offline — without this, OBS_V3 policies eval with the
    forecast features zeroed while they were trained with them populated.

    Everything observed derives from the sim itself (engine price, clock), so
    episodes stay deterministic. The engine price doubles as the grid-price
    proxy: the bench has no external market, and a never-observed price_real
    model would encode as a poisoned zero once the service is warm."""
    service = sim.forecast_service
    if service is None:
        return
    last = sim.engine.last_price
    price = None if last is None else float(last)
    service.observe(sim_ts, price_p2p=price, price_real=price, ghi=_bench_ghi(sim_ts))
    service.refresh(sim_ts)


def run_episode(
    make_agent,
    *,
    n_ticks: int,
    tick_h: float,
    forecasts_enabled: bool = True,
    episode_seed: int = 0,
    candidate_params: VPPParams | None = None,
) -> tuple[Simulator, object]:
    """One episode: counter-roster + the candidate in the test slot, stepped n_ticks
    through the exact gate path the live loop uses."""
    sim = Simulator(bus=InMemoryBus(), sim_epoch=BENCH_EPOCH)
    # Let resting orders survive a few ticks (the live default TTL is tuned for 1s
    # ticks; at the bench's coarser cadence it would expire orders within their own
    # tick and kill cross-tick liquidity).
    sim.order_ttl_sec = tick_h * 3600.0 * 4
    if forecasts_enabled:
        sim.forecast_service = ForecastService()
    for spec in counter_roster():
        sim.add_builtin_vpp(
            spec.name,
            spec.params,
            spec.agent,
            seed=_episode_vpp_seed(episode_seed, spec.seed),
        )
    try:
        candidate = make_agent()
        test_vpp = sim.add_builtin_vpp(
            "agent-under-test",
            candidate_params or test_slot_params(),
            candidate,
            seed=_episode_vpp_seed(episode_seed, 99),
        )
    except Exception as exc:
        raise CandidateEpisodeError("candidate construction failed") from exc

    sim_ts = sim.clock.now_sim()
    step = timedelta(seconds=tick_h * 3600.0)
    for _ in range(n_ticks):
        _observe_and_refresh_forecast(sim, sim_ts)
        sim._expire_orders(sim_ts)
        market = MarketSnapshot.from_engine(sim_ts, sim.engine.snapshot(depth_levels=5))
        net = sim._open_orders_net_by_vpp()
        counts = sim._open_order_counts_by_vpp()
        for vpp in sim.vpps.values():
            try:
                sim._tick_vpp(
                    vpp,
                    sim_ts,
                    tick_h,
                    market,
                    open_orders_net_kwh=net.get(vpp.vpp_id, 0.0),
                    open_order_count=counts.get(vpp.vpp_id, 0),
                )
            except Exception as exc:
                if vpp is test_vpp:
                    raise CandidateEpisodeError("candidate execution failed") from exc
                raise
        sim_ts = sim_ts + step
    return sim, test_vpp


def _episode_vpp_seed(episode_seed: int, vpp_seed: int) -> int:
    """Mix an official episode seed into each stable per-VPP benchmark seed."""
    return ((int(episode_seed) * 1_000_003) ^ int(vpp_seed)) & 0xFFFFFFFF


def measure_episode(name: str, sim: Simulator, vpp, n_ticks: int) -> EpisodeMetrics:
    """Measure any VPP from a completed benchmark episode."""
    last = sim.engine.last_price
    pending = vpp.state.pending_net_kwh
    realized = float(vpp.state.pnl)
    price_ref = float(last) if last is not None else 0.0
    mtm = realized + (pending + vpp.battery.soc_kwh) * price_ref
    open_net = sim._open_orders_net_by_vpp().get(vpp.vpp_id, 0.0)
    return EpisodeMetrics(
        candidate=name,
        realized_pnl=realized,
        mark_to_market=mtm,
        energy_bought_kwh=vpp.state.cumulative_energy_bought_kwh,
        energy_sold_kwh=vpp.state.cumulative_energy_sold_kwh,
        unresolved_imbalance_kwh=abs(pending + open_net),
        final_soc_frac=vpp.battery.soc_frac,
        risk_rejections=sim.risk_rejections_by_vpp.get(vpp.vpp_id, 0),
        n_ticks=n_ticks,
    )


def score(
    name: str,
    make_agent,
    *,
    n_ticks: int,
    tick_h: float,
    forecasts_enabled: bool = True,
    episode_seed: int = 0,
    candidate_params: VPPParams | None = None,
) -> EpisodeMetrics:
    """Run one candidate through an episode and measure it (shared by the benchmark
    and the PPO eval so they score identically)."""
    sim, vpp = run_episode(
        make_agent,
        n_ticks=n_ticks,
        tick_h=tick_h,
        forecasts_enabled=forecasts_enabled,
        episode_seed=episode_seed,
        candidate_params=candidate_params,
    )
    return measure_episode(name, sim, vpp, n_ticks)


def run_benchmark(
    *, n_ticks: int = 144, tick_minutes: float = 10.0, forecasts_enabled: bool = True
) -> list[EpisodeMetrics]:
    """Score every candidate on the fixed scenario. Default: a full sim-day at 10-min
    ticks (144 ticks)."""
    tick_h = tick_minutes / 60.0
    return [
        score(name, make, n_ticks=n_ticks, tick_h=tick_h, forecasts_enabled=forecasts_enabled)
        for name, make in candidates().items()
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="EFlux agent benchmark — each candidate vs a fixed counter-roster.")
    ap.add_argument("--ticks", type=int, default=144, help="number of ticks per episode")
    ap.add_argument("--tick-minutes", type=float, default=10.0, help="sim-minutes per tick")
    ap.add_argument(
        "--no-forecast",
        action="store_true",
        help="episode without forecast channels (legacy zero-forecast behavior, for A/B)",
    )
    args = ap.parse_args()
    rows = run_benchmark(
        n_ticks=args.ticks,
        tick_minutes=args.tick_minutes,
        forecasts_enabled=not args.no_forecast,
    )
    print(format_leaderboard(rows))


if __name__ == "__main__":
    main()
