"""Simulator runner: drives the matching engine + in-process built-in VPPs.

External (SDK) VPPs use the same engine through the REST/WS API. Concurrent submitters
share an asyncio.Lock to keep the (sync) matching engine race-free.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from eflux.agents.base import AgentContext, BaseAgent, MarketSnapshot, OrderIntent
from eflux.agents.hybrid import RiskGate, RiskLimits, RiskRejected
from eflux.bridge.bus import EventBus
from eflux.config import get_settings
from eflux.market.clock import RollingClock
from eflux.market.events import EventKind, TickEvent, TradeEvent
from eflux.market.matching_engine import MatchingEngine
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad, WindTurbine

log = logging.getLogger(__name__)


@dataclass
class SimulatorVPP:
    vpp_id: int
    name: str
    params: VPPParams
    agent: BaseAgent
    strategy: str
    is_my_vpp: bool
    llm_live: bool
    llm_status: str
    state: VPPState
    pv: PV
    battery: Battery
    load: FlexibleLoad
    wind: WindTurbine | None = None
    rng: random.Random = field(default_factory=random.Random)
    open_order_ids: list[int] = field(default_factory=list)
    recent_trades: list[dict] = field(default_factory=list)


class Simulator:
    def __init__(self, bus: EventBus, sim_epoch: datetime | None = None) -> None:
        settings = get_settings()
        self.bus = bus
        # Rolling log of recent trades so late-joining clients (page loads,
        # WS reconnects) can backfill instead of starting from a blank chart.
        self.trade_log: deque[TradeEvent] = deque(maxlen=500)
        self.engine = MatchingEngine(publish_cb=self._publish_event)
        self.clock = RollingClock(
            sim_epoch=sim_epoch or _default_sim_epoch(settings.site_timezone),
            speed=settings.market_speed,
            tick_sim_sec=settings.market_tick_sec,
        )
        self.vpps: dict[int, SimulatorVPP] = {}
        self.order_ttl_sec: float = settings.order_ttl_sec
        # Single hard-constraint authority every order (built-in, learned, fallback,
        # external) passes through before the engine — see agents/hybrid/risk.py.
        # The max-open-orders cap is derived from the order TTL: a VPP requotes its
        # balance as it re-accumulates, and each quote rests for the TTL, so one VPP
        # legitimately holds ~order_ttl_sec/tick_sec resting orders in steady state.
        # Size the cap above that natural ceiling (with headroom) so the gate never
        # clips the existing market — it only bounds genuine runaway/abuse.
        tick_sec = max(settings.market_tick_sec, 1e-9)
        ttl_ticks = settings.order_ttl_sec / tick_sec if settings.order_ttl_sec > 0 else 0.0
        self.risk_gate = RiskGate(RiskLimits(max_open_orders=max(256, int(ttl_ticks) + 64)))
        # Orders the gate vetoed — a global total plus a per-VPP breakdown (the
        # benchmark's invalid-action metric must be attributable to one candidate).
        self.risk_rejections = 0
        self.risk_rejections_by_vpp: dict[int, int] = {}
        # tick_h of the tick currently being processed. Trade settlement reads this
        # so battery charge/discharge use the actual tick duration, not a global
        # default — they diverge whenever the loop is stepped at a non-default
        # cadence (e.g. the benchmark's coarse ticks).
        self._current_tick_h: float = tick_sec / 3600.0
        # Site-weather memo: several VPPs share coords (e.g. the HKU rooftop),
        # and today/future forecast days are deliberately not disk-cached.
        self._site_weather_cache: dict[tuple[float, float], object] = {}
        self._next_vpp_id = -1  # internal VPPs use negative ids to avoid clashing with DB ids
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._data_source_status: dict | None = None

    def _publish_event(self, event) -> None:
        if isinstance(event, TradeEvent):
            self.trade_log.append(event)
        self.bus.publish(event)

    def add_builtin_vpp(
        self,
        name: str,
        params: VPPParams,
        agent: BaseAgent,
        *,
        seed: int = 0,
        strategy: str | None = None,
        is_my_vpp: bool = False,
        llm_live: bool = False,
        llm_status: str = "",
    ) -> SimulatorVPP:
        vpp_id = self._next_vpp_id
        self._next_vpp_id -= 1
        # One site-weather fetch feeds both the pvlib PV model and real wind speeds.
        site_weather = self._fetch_site_weather(params)
        wind = None
        if params.wind_kw_rated > 0:
            wind = WindTurbine(
                rated_kw=params.wind_kw_rated,
                mean_wind=params.wind_mean_speed,
            )
            wind.weather = site_weather
        vpp = SimulatorVPP(
            vpp_id=vpp_id,
            name=name,
            params=params,
            agent=agent,
            strategy=strategy or agent.__class__.__name__,
            is_my_vpp=is_my_vpp,
            llm_live=llm_live,
            llm_status=llm_status,
            state=VPPState(sim_ts=self.clock.now_sim(), soc_kwh=params.battery_kwh * 0.5),
            pv=PV(
                kw_peak=params.pv_kw_peak,
                noise_std=params.forecast_noise_std,
                physical_model=self._build_pv_physical_model(params, site_weather),
            ),
            battery=Battery(
                capacity_kwh=params.battery_kwh,
                max_power_kw=params.battery_kw_max,
                eta_rt=params.battery_eta_rt,
                soc_kwh=params.battery_kwh * 0.5,
            ),
            load=FlexibleLoad(
                base_kw=params.load_kw_base,
                elasticity=params.load_elasticity,
                profile=params.load_profile,
            ),
            wind=wind,
            rng=random.Random(seed),
        )
        self.vpps[vpp_id] = vpp
        log.info("Added built-in VPP id=%d name=%s", vpp_id, name)
        return vpp

    def my_managed_vpps(self) -> list[SimulatorVPP]:
        return [vpp for vpp in self.vpps.values() if vpp.is_my_vpp]

    # How long a data-source check stays fresh before data_source_status()
    # re-inspects weather coverage (cheap, purely in-memory).
    DATA_SOURCE_TTL_SEC = 60.0

    def refresh_data_sources(self) -> None:
        """Check which data source each built-in VPP is currently using."""
        checked_at = datetime.now(UTC)
        sim_ts = self.clock.now_sim()
        sources: list[dict[str, str]] = []
        for vpp in self.vpps.values():
            # Only report components a VPP actually has — with 30 VPPs the
            # banner would otherwise drown in "no PV configured" noise.
            if vpp.params.pv_kw_peak > 0:
                sources.append(self._pv_source_for(vpp, sim_ts))
            if vpp.wind is not None:
                sources.append(self._wind_source_for(vpp, sim_ts))
        active_real = [s for s in sources if s["status"] == "real"]
        fallback = [s for s in sources if s["status"] == "fallback"]

        if active_real and not fallback:
            summary = "Open-Meteo + pvlib"
        elif active_real and fallback:
            summary = "Mixed PV sources"
        elif fallback:
            summary = "Synthetic PV fallback"
        else:
            summary = "Synthetic profiles"

        self._data_source_status = {
            "checked_at": checked_at,
            "sim_ts": sim_ts,
            "summary": summary,
            "sources": sources,
        }

    def data_source_status(self) -> dict:
        """Current status, re-checked when stale — the sim clock keeps moving, so
        'does the weather cover the current sim hour' is a moving target."""
        current = self._data_source_status
        if current is None or (
            datetime.now(UTC) - current["checked_at"]
        ).total_seconds() > self.DATA_SOURCE_TTL_SEC:
            self.refresh_data_sources()
        return self._data_source_status or {}

    def _pv_source_for(self, vpp: SimulatorVPP, sim_ts: datetime) -> dict[str, str]:
        model = vpp.pv.physical_model
        component = f"{vpp.name} PV"
        if model is None:
            return {
                "component": component,
                "status": "synthetic",
                "source": "Diurnal sine stub",
                "detail": "No PV latitude/longitude configured for this VPP.",
            }

        weather = getattr(model, "weather", None)
        if weather is None or getattr(weather, "empty", False):
            return {
                "component": component,
                "status": "fallback",
                "source": "Diurnal sine stub",
                "detail": "Open-Meteo weather was unavailable at startup.",
            }

        target = sim_ts.replace(minute=0, second=0, microsecond=0)
        index = getattr(weather, "index", [])
        if target in index:
            return {
                "component": component,
                "status": "real",
                "source": "Open-Meteo + pvlib",
                "detail": f"Weather row matched current sim hour {target.isoformat()}.",
            }

        try:
            coverage = f"{index.min().isoformat()} to {index.max().isoformat()}"
        except Exception:
            coverage = "unknown coverage"
        return {
            "component": component,
            "status": "fallback",
            "source": "Diurnal sine stub",
            "detail": (
                "Open-Meteo data loaded, but it does not cover current sim hour "
                f"{target.isoformat()} (coverage: {coverage})."
            ),
        }

    def _fetch_site_weather(self, params: VPPParams):
        """Open-Meteo hourly weather for the VPP's site coords, or None.

        The live simulator runs at (roughly) wall-clock time, so the window
        must cover *now*: recent past for context plus a couple of forecast
        days ahead. weather.py picks the forecast endpoint for ranges touching
        today (the archive lags real-time and can't). The same DataFrame
        drives the pvlib PV model and real wind speeds (wind_speed column).
        """
        if params.pv_lat is None or params.pv_lon is None:
            return None
        key = (round(params.pv_lat, 4), round(params.pv_lon, 4))
        if key in self._site_weather_cache:
            return self._site_weather_cache[key]
        try:
            from datetime import date, timedelta

            from eflux.data.weather import fetch_hourly_sync
        except ImportError as e:
            log.warning("Site coords set but 'data' extra missing (%s) — using stub models", e)
            return None
        try:
            today = date.today()
            weather = fetch_hourly_sync(
                params.pv_lat, params.pv_lon, today - timedelta(days=2), today + timedelta(days=2)
            )
            log.info(
                "Site weather attached for (%.2f, %.2f), %d rows",
                params.pv_lat, params.pv_lon, len(weather),
            )
            self._site_weather_cache[key] = weather
            return weather
        except Exception:
            log.exception("Weather pre-fetch failed — DER models fall back to stubs")
            return None

    def _wind_source_for(self, vpp: SimulatorVPP, sim_ts: datetime) -> dict[str, str]:
        component = f"{vpp.name} wind"
        weather = vpp.wind.weather if vpp.wind is not None else None
        if weather is None or getattr(weather, "empty", False):
            return {
                "component": component,
                "status": "synthetic",
                "source": "AR(1) gust stub",
                "detail": f"Stub wind around {vpp.params.wind_mean_speed:.1f} m/s (no site weather).",
            }
        target = sim_ts.replace(minute=0, second=0, microsecond=0)
        if target in getattr(weather, "index", []):
            return {
                "component": component,
                "status": "real",
                "source": "Open-Meteo wind",
                "detail": f"Wind speed row matched current sim hour {target.isoformat()}.",
            }
        try:
            index = weather.index
            coverage = f"{index.min().isoformat()} to {index.max().isoformat()}"
        except Exception:
            coverage = "unknown coverage"
        return {
            "component": component,
            "status": "fallback",
            "source": "AR(1) gust stub",
            "detail": (
                f"Site weather loaded but does not cover current sim hour "
                f"{target.isoformat()} (coverage: {coverage})."
            ),
        }

    def _build_pv_physical_model(self, params: VPPParams, site_weather):
        """pvlib model fed by the site weather; None when not applicable."""
        if site_weather is None or params.pv_kw_peak <= 0:
            return None
        try:
            from eflux.data.pv_model import PVPhysicalModel
        except ImportError:
            return None
        model = PVPhysicalModel(
            lat=params.pv_lat,
            lon=params.pv_lon,
            kw_peak=params.pv_kw_peak,
            tilt=params.pv_tilt,
            azimuth=params.pv_azimuth,
        )
        model.weather = site_weather
        return model

    async def submit_external(
        self,
        *,
        vpp_id: int,
        side: str,
        price: Decimal,
        qty: Decimal,
    ) -> dict:
        """Entry point for SDK-submitted orders. Honors realtime-only constraint."""
        if not self.clock.is_realtime:
            raise PermissionError(
                f"external orders rejected: market speed is {self.clock.speed}x (realtime required)"
            )
        async with self._lock:
            now_sim = self.clock.now_sim()
            now_wall = datetime.now(UTC)
            # Same gate as built-in agents (principle #7). External VPPs carry no
            # in-memory battery/DER state here, so only the universal static and
            # rate limits apply; ownership was already checked at the REST layer.
            decision = self.risk_gate.validate(
                [OrderIntent(side=side, price=price, qty=qty)],
                vpp_id=vpp_id,
                open_order_count=self._open_order_counts_by_vpp().get(vpp_id, 0),
            )
            if not decision.accepted:
                self._record_rejections(vpp_id, len(decision.rejected) or 1)
                reason = decision.rejected[0].reason if decision.rejected else "rejected by risk gate"
                raise RiskRejected(reason)
            result = self.engine.submit(
                vpp_id=vpp_id,
                side=side,
                price=price,
                qty=qty,
                sim_ts=now_sim,
                wall_ts=now_wall,
                ttl_sec=self.order_ttl_sec or None,
            )
            self._record_trades(result.trades)
        return {
            "order_id": result.order.order_id,
            "remaining_qty": str(result.order.remaining_qty),
            # Surface the TTL so API integrators know unfilled remainders are
            # swept at this sim time (order.cancelled event) instead of resting.
            "expires_at_sim": result.order.expires_at,
            "trades": [t.model_dump(mode="json") for t in result.trades],
        }

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="simulator-loop")
        self._task.add_done_callback(_log_unexpected_loop_exit)

    async def stop(self) -> None:
        self.clock.stop()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except TimeoutError:
                self._task.cancel()
            self._task = None
        await self._shutdown_reflections()

    async def _shutdown_reflections(self) -> None:
        """Cancel in-flight LLM reflection tasks and close the shared client.

        An LLM round-trip can take minutes; without this, shutdown mid-call
        leaves a 'Task was destroyed but it is pending!' warning and the shared
        httpx client's connections leak."""
        pending: list[asyncio.Task] = []
        clients = set()
        for vpp in self.my_managed_vpps():
            task = getattr(vpp.agent, "_reflection_task", None)
            if task is not None and not task.done():
                task.cancel()
                pending.append(task)
            client = getattr(vpp.agent, "llm_client", None)
            if client is not None:
                clients.add(client)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for client in clients:
            aclose = getattr(client, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    log.exception("LLM client close failed during shutdown")

    async def _run(self) -> None:
        log.info("Simulator loop started (speed=%sx, tick=%ss)", self.clock.speed, self.clock.tick_sim_sec)
        tick_h = self.clock.tick_sim_sec / 3600.0
        async for tick_no, sim_ts in self.clock.ticks():
            async with self._lock:
                self._expire_orders(sim_ts)
                snapshot = self.engine.snapshot(depth_levels=5)
                market_snap = MarketSnapshot.from_engine(sim_ts, snapshot)
                market_snap.recent_trades = self._recent_market_trades()
                market_snap.peer_reflections = self._peer_reflections()
                open_orders_net = self._open_orders_net_by_vpp()
                open_order_counts = self._open_order_counts_by_vpp()
                # Step each built-in VPP. One faulty agent (bad agent_params,
                # DER model edge case) must not kill the whole market loop.
                for vpp in self.vpps.values():
                    try:
                        self._tick_vpp(
                            vpp,
                            sim_ts,
                            tick_h,
                            market_snap,
                            open_orders_net_kwh=open_orders_net.get(vpp.vpp_id, 0.0),
                            open_order_count=open_order_counts.get(vpp.vpp_id, 0),
                        )
                    except Exception:
                        log.exception("VPP %s (%d) tick failed — skipping this tick", vpp.name, vpp.vpp_id)
                # Publish a tick event with current market summary.
                bb = self.engine.book.best_bid()
                ba = self.engine.book.best_ask()
                self.bus.publish(
                    TickEvent(
                        kind=EventKind.TICK,
                        sim_ts=sim_ts,
                        wall_ts=datetime.now(UTC),
                        tick_no=tick_no,
                        best_bid=bb.price if bb else None,
                        best_ask=ba.price if ba else None,
                        last_price=self.engine.last_price,
                        bid_depth=bb.total_qty if bb else Decimal("0"),
                        ask_depth=ba.total_qty if ba else Decimal("0"),
                    )
                )

    def _recent_market_trades(self, limit: int = 8) -> list[dict]:
        """Latest market-wide fills with party names — prompt context for
        learning agents (who is trading with whom, and at what price)."""
        out: list[dict] = []
        for t in list(self.trade_log)[-limit:]:
            buyer = self.vpps.get(t.buy_vpp_id)
            seller = self.vpps.get(t.sell_vpp_id)
            out.append(
                {
                    "price": float(t.price),
                    "qty": float(t.qty),
                    "buyer": buyer.name if buyer else f"external-{t.buy_vpp_id}",
                    "seller": seller.name if seller else f"external-{t.sell_vpp_id}",
                }
            )
        return out

    def _peer_reflections(self) -> list[dict]:
        """Each LLM agent's latest successful reflection, for the other LLM
        agents' prompts. Tagged with vpp_id so an agent can drop its own."""
        out: list[dict] = []
        for vpp in self.my_managed_vpps():
            entries = getattr(vpp.agent, "reflection_log", None)
            if not entries:
                continue
            last_ok = next((e for e in reversed(entries) if e.get("ok")), None)
            if last_ok is None:
                continue
            out.append(
                {
                    "vpp_id": vpp.vpp_id,
                    "name": vpp.name,
                    "pa": last_ok["price_adjust"],
                    "qs": last_ok["qty_scale"],
                    "rationale": last_ok["rationale"],
                }
            )
        return out

    def _open_orders_net_by_vpp(self) -> dict[int, float]:
        """Signed resting (non-dispatched) book exposure per VPP: sell
        remainders +, buy remainders - (the pending_net_kwh convention).
        Lets agents see their true unserved position — pending alone is the
        post-debit balance. One book walk per tick; depth is TTL-bounded."""
        out: dict[int, float] = {}
        for side in ("buy", "sell"):
            for order in self.engine.book.iter_orders(side):
                if order.dispatched:
                    continue
                signed = (
                    float(order.remaining_qty)
                    if order.side == "sell"
                    else -float(order.remaining_qty)
                )
                out[order.vpp_id] = out.get(order.vpp_id, 0.0) + signed
        return out

    def _open_order_counts_by_vpp(self) -> dict[int, int]:
        """Live count of resting orders per VPP (the authoritative open-order
        count for the RiskGate's max-open-orders limit). Counted from the book
        rather than vpp.open_order_ids, which only sheds ids on TTL expiry."""
        counts: dict[int, int] = {}
        for side in ("buy", "sell"):
            for order in self.engine.book.iter_orders(side):  # type: ignore[arg-type]
                counts[order.vpp_id] = counts.get(order.vpp_id, 0) + 1
        return counts

    def _tick_vpp(
        self,
        vpp: SimulatorVPP,
        sim_ts: datetime,
        tick_h: float,
        market: MarketSnapshot,
        *,
        open_orders_net_kwh: float = 0.0,
        open_order_count: int = 0,
    ) -> None:
        # Refresh DER state.
        vpp.pv.kw_peak = vpp.params.pv_kw_peak  # keep params live-editable later
        vpp.state.sim_ts = sim_ts
        vpp.state.pv_kw = vpp.pv.output_kw(sim_ts, vpp.rng)
        vpp.state.wind_kw = vpp.wind.output_kw(sim_ts, vpp.rng) if vpp.wind else 0.0
        vpp.state.load_kw = vpp.load.draw_kw(sim_ts, vpp.rng)
        vpp.state.update_net()
        # Credit this tick's net energy to the untraded balance. Clamped to the
        # battery capacity on either side: if orders rest unfilled for a long
        # stretch, the physical buffer is what bounds how much energy can pile up.
        cap = max(vpp.params.battery_kwh, 1.0)
        vpp.state.pending_net_kwh = min(
            cap, max(-cap, vpp.state.pending_net_kwh + vpp.state.net_kw * tick_h)
        )

        # Trade settlement (battery charge/discharge) reads this so it uses the
        # cadence the loop is actually stepping at, not the configured default.
        self._current_tick_h = tick_h
        ctx = AgentContext(
            vpp_id=vpp.vpp_id,
            params=vpp.params,
            state=vpp.state,
            pv=vpp.pv,
            battery=vpp.battery,
            load=vpp.load,
            market=market,
            rng=vpp.rng,
            tick_duration_h=tick_h,
            open_orders_net_kwh=open_orders_net_kwh,
        )
        intents = vpp.agent.decide(ctx)
        self._gate_and_submit(vpp, ctx, intents, sim_ts, open_order_count)

    def _gate_and_submit(
        self,
        vpp: SimulatorVPP,
        ctx: AgentContext,
        intents: list[OrderIntent],
        sim_ts: datetime,
        open_order_count: int,
    ) -> None:
        """Run the VPP's order batch through the RiskGate, then submit what survives.
        If every order is vetoed and the agent exposes a `risk_fallback` policy, the
        fallback's (re-gated) action is submitted instead — the safe-action path for
        a learned policy that produced an out-of-envelope batch."""
        decision = self.risk_gate.validate(
            intents,
            vpp_id=vpp.vpp_id,
            params=vpp.params,
            battery=vpp.battery,
            tick_h=ctx.tick_duration_h,
            open_order_count=open_order_count,
        )
        self._record_rejections(vpp.vpp_id, len(decision.rejected))
        if decision.requires_fallback:
            fallback = getattr(vpp.agent, "risk_fallback", None)
            if fallback is not None:
                decision = self.risk_gate.validate(
                    fallback.decide(ctx),
                    vpp_id=vpp.vpp_id,
                    params=vpp.params,
                    battery=vpp.battery,
                    tick_h=ctx.tick_duration_h,
                    open_order_count=open_order_count,
                )
                self._record_rejections(vpp.vpp_id, len(decision.rejected))
        for intent in decision.accepted:
            self._submit_intent(vpp, intent, sim_ts)

    def _record_rejections(self, vpp_id: int, n: int) -> None:
        if n:
            self.risk_rejections += n
            self.risk_rejections_by_vpp[vpp_id] = self.risk_rejections_by_vpp.get(vpp_id, 0) + n

    def _expire_orders(self, sim_ts: datetime) -> None:
        """Expire TTL'd resting orders; refund the unfilled remainder to the
        owner's accumulator. The agent 'spoke for' that energy at submit time
        (the debit in _submit_intent) and it was never delivered/received —
        without the refund agents permanently understate their position."""
        expired = self.engine.expire(sim_ts=sim_ts, wall_ts=datetime.now(UTC))
        for order in expired:
            vpp = self.vpps.get(order.vpp_id)
            if vpp is None:
                continue  # external order — the owner re-quotes on its own
            if order.order_id in vpp.open_order_ids:
                vpp.open_order_ids.remove(order.order_id)
            if order.dispatched:
                continue  # battery-band/gas quotes never touched the accumulator
            signed = (
                float(order.remaining_qty) if order.side == "sell" else -float(order.remaining_qty)
            )
            cap = max(vpp.params.battery_kwh, 1.0)
            vpp.state.pending_net_kwh = min(
                cap, max(-cap, vpp.state.pending_net_kwh + signed)
            )

    def market_balance(self) -> dict:
        """Aggregate live supply/demand across built-in VPPs plus book depth —
        the instrument for judging whether the market is structurally balanced."""
        renewable_kw = sum(v.state.pv_kw + v.state.wind_kw for v in self.vpps.values())
        load_kw = sum(v.state.load_kw for v in self.vpps.values())
        gas_capacity_kw = sum(v.params.gas_kw_max for v in self.vpps.values())
        ratio = (renewable_kw + gas_capacity_kw) / load_kw if load_kw > 1e-6 else None
        bid_depth = sum(q for _, q in self.engine.book.depth("buy", 10**6))
        ask_depth = sum(q for _, q in self.engine.book.depth("sell", 10**6))
        return {
            "renewable_kw": round(renewable_kw, 3),
            "load_kw": round(load_kw, 3),
            "gas_capacity_kw": round(gas_capacity_kw, 3),
            "net_kw": round(renewable_kw - load_kw, 3),
            "supply_demand_ratio": round(ratio, 4) if ratio is not None else None,
            "bid_depth_kwh": float(bid_depth),
            "ask_depth_kwh": float(ask_depth),
        }

    def _submit_intent(self, vpp: SimulatorVPP, intent: OrderIntent, sim_ts: datetime) -> None:
        try:
            result = self.engine.submit(
                vpp_id=vpp.vpp_id,
                side=intent.side,
                price=intent.price,
                qty=intent.qty,
                sim_ts=sim_ts,
                wall_ts=datetime.now(UTC),
                ttl_sec=self.order_ttl_sec or None,
                dispatched=intent.dispatched,
            )
            # Debit the untraded balance for the quoted quantity — the agent has
            # now "spoken for" that energy, whether or not the order fills.
            # Battery-band quotes settle through the battery, not the PV-load
            # imbalance, so they leave the accumulator alone.
            if not intent.dispatched:
                signed = -float(intent.qty) if intent.side == "sell" else float(intent.qty)
                vpp.state.pending_net_kwh += signed
            if result.order.remaining_qty > 0:
                vpp.open_order_ids.append(result.order.order_id)
            self._record_trades(result.trades)
        except ValueError:
            log.exception("VPP %s submitted an invalid order", vpp.vpp_id)

    def _record_trades(self, trades: list[TradeEvent]) -> None:
        for trade in trades:
            self._apply_trade_to_vpp(trade, side="buy")
            self._apply_trade_to_vpp(trade, side="sell")

    def _apply_trade_to_vpp(self, trade: TradeEvent, *, side: str) -> None:
        vpp_id = trade.buy_vpp_id if side == "buy" else trade.sell_vpp_id
        vpp = self.vpps.get(vpp_id)
        if vpp is None:
            return

        qty_f = float(trade.qty)
        cash = Decimal(str(float(trade.price) * qty_f))
        tick_h = self._current_tick_h
        counterparty = trade.sell_vpp_id if side == "buy" else trade.buy_vpp_id

        if side == "buy":
            vpp.state.pnl -= cash
            vpp.state.cumulative_energy_bought_kwh += qty_f
            vpp.battery.charge(power_kw=qty_f / max(1e-9, tick_h), duration_h=tick_h)
        else:
            vpp.state.pnl += cash
            vpp.state.cumulative_energy_sold_kwh += qty_f
            vpp.battery.discharge(power_kw=qty_f / max(1e-9, tick_h), duration_h=tick_h)

        record = {
            "trade_id": trade.trade_id,
            "side": side,
            "price": str(trade.price),
            "qty": str(trade.qty),
            "cash": str(cash),
            "counterparty_vpp_id": counterparty,
            "buy_vpp_id": trade.buy_vpp_id,
            "sell_vpp_id": trade.sell_vpp_id,
            "sim_ts": trade.sim_ts,
            "wall_ts": trade.wall_ts,
        }
        vpp.recent_trades.insert(0, record)
        vpp.recent_trades = vpp.recent_trades[:50]
        record_trade = getattr(vpp.agent, "record_trade", None)
        if callable(record_trade):
            record_trade(record)


def _log_unexpected_loop_exit(task: asyncio.Task) -> None:
    """Surface a dead simulator loop immediately — otherwise the market would
    freeze silently (stale snapshots, no ticks) until shutdown re-raises."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("Simulator loop died unexpectedly", exc_info=exc)


def _default_sim_epoch(site_timezone: str) -> datetime:
    """Start demo DER profiles on local site time, not UTC wall-clock hour."""
    return datetime.now(ZoneInfo(site_timezone)).replace(microsecond=0)
