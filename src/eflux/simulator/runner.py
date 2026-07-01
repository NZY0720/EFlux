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
from typing import TYPE_CHECKING, Literal, TypedDict
from zoneinfo import ZoneInfo

from eflux.agents.base import AgentContext, BaseAgent, MarketSnapshot, OrderIntent
from eflux.agents.hybrid import RiskGate, RiskLimits, RiskRejected
from eflux.agents.reflective.chat import build_chat_messages, clean_chat_line
from eflux.bridge.bus import EventBus
from eflux.config import get_settings
from eflux.data.electricity_market import (
    CaisoOasisClient,
    ExternalMarketQuote,
    disabled_quote,
    synthetic_quote,
)
from eflux.market.clock import RollingClock
from eflux.market.events import EventKind, ExternalTradeEvent, TickEvent, TradeEvent
from eflux.market.matching_engine import MatchingEngine
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad, WindTurbine

if TYPE_CHECKING:
    from eflux.agents.reflective.pool import SharedLLM

log = logging.getLogger(__name__)

# Agent chatroom cadence: every N ticks one LLM agent (round-robin) posts a line. Bounded
# further by a single in-flight chat task + the shared strategist gate, so cost stays low.
CHAT_INTERVAL_TICKS = 15
# Conversation balance: base chance a post replies to recent chat (agents always reply when
# mentioned), capped so the room can't devolve into an endless reply chain with no new topics.
CHAT_REPLY_PROB = 0.45
CHAT_MAX_REPLY_STREAK = 3


def _agent_chat_client(agent: BaseAgent):
    """The agent's strategist LLM client (its deployed model), or None for non-LLM agents."""
    strategist = getattr(agent, "strategist", None)
    return getattr(strategist, "client", None) if strategist is not None else None


PpoRenewState = Literal["idle", "training", "reloading", "done", "error"]


class ExternalSubmitResult(TypedDict):
    order_id: int
    remaining_qty: str
    expires_at_sim: datetime | None
    trades: list[dict[str, object]]


class MarketBalanceSummary(TypedDict):
    renewable_kw: float
    load_kw: float
    gas_capacity_kw: float
    net_kw: float
    supply_demand_ratio: float | None
    bid_depth_kwh: float
    ask_depth_kwh: float


class PpoRenewStatus(TypedDict):
    state: PpoRenewState
    started_at: str | None
    finished_at: str | None
    detail: str
    reloaded: int
    error: str | None
    metrics: dict[str, object] | None


@dataclass
class SimulatorVPP:
    vpp_id: int
    name: str
    params: VPPParams
    agent: BaseAgent
    strategy: str
    is_my_vpp: bool
    mirror_of: str | None
    llm_live: bool
    llm_status: str
    state: VPPState
    pv: PV
    battery: Battery
    load: FlexibleLoad
    wind: WindTurbine | None = None
    # The DB user who provisioned this managed agent via the API. None for built-in /
    # house roster agents. Scopes my_managed_vpps() so a user sees only their own.
    owner_id: int | None = None
    # Stable DB id (vpps.id) of the managed-agent definition this VPP was provisioned from, so
    # the API can address it across restarts (the negative vpp_id is reassigned on each boot).
    managed_def_id: int | None = None
    rng: random.Random = field(default_factory=random.Random)
    open_order_ids: list[int] = field(default_factory=list)
    recent_trades: list[dict] = field(default_factory=list)
    trade_count: int = 0


class Simulator:
    def __init__(self, bus: EventBus, sim_epoch: datetime | None = None) -> None:
        settings = get_settings()
        self.bus = bus
        # Rolling log of recent trades so late-joining clients (page loads,
        # WS reconnects) can backfill instead of starting from a blank chart.
        self.trade_log: deque[TradeEvent | ExternalTradeEvent] = deque(maxlen=500)
        self.engine = MatchingEngine(publish_cb=self._publish_event)
        self.clock = RollingClock(
            sim_epoch=sim_epoch or _default_sim_epoch(settings.site_timezone),
            speed=settings.market_speed,
            tick_sim_sec=settings.market_tick_sec,
        )
        self.vpps: dict[int, SimulatorVPP] = {}
        # One Semaphore-gated LLM connection shared by every managed agent, retained so
        # the API can provision new managed agents at runtime. Set by the scenario loader
        # at startup (None until then).
        self.shared_llm: SharedLLM | None = None
        # Agent chatroom — LLM agents post casual, in-character small talk (in-memory, like
        # the rest of market state). Round-robin index + at most one chat call in flight.
        self.chatter: deque[dict] = deque(maxlen=80)
        self._chat_rr = 0
        self._chat_reply_streak = 0
        self._chat_task: asyncio.Task | None = None
        # "p2p" = peer-to-peer CDA (agents trade each other; CAISO is reference-only).
        # "realprice" = pure price-taking against the live CAISO price (orders settle
        # vs the grid, no peer matching). Selected per launch via EFLUX_MARKET_MODE.
        self.market_mode: str = settings.market_mode
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
        self._next_external_trade_id = 1
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._external_market_task: asyncio.Task | None = None
        # Background "renew PPOs" (retrain on latest real data + hot-reload) state.
        self._ppo_renew_task: asyncio.Task | None = None
        self._ppo_renew: PpoRenewStatus = {
            "state": "idle",  # idle | training | reloading | done | error
            "started_at": None,
            "finished_at": None,
            "detail": "",
            "reloaded": 0,
            "error": None,
            "metrics": None,
        }
        self._data_source_status: dict | None = None
        fallback_price = Decimal(str(settings.external_market_fallback_price))
        if settings.external_market_enabled:
            self.external_market_client = CaisoOasisClient()
            self._external_market_quote: ExternalMarketQuote = synthetic_quote(
                region=settings.market_region,
                node=settings.external_market_node,
                price=fallback_price,
                detail="Waiting for first CAISO OASIS refresh.",
            )
        else:
            self.external_market_client = None
            self._external_market_quote = disabled_quote(
                region=settings.market_region,
                node=settings.external_market_node,
                fallback_price=fallback_price,
            )

    def _publish_event(self, event) -> None:
        if isinstance(event, (TradeEvent, ExternalTradeEvent)):
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
        owner_id: int | None = None,
        mirror_of: str | None = None,
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
            owner_id=owner_id,
            mirror_of=mirror_of,
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

    def my_managed_vpps(self, owner_id: int | None = None) -> list[SimulatorVPP]:
        """Managed (is_my_vpp) agents. With owner_id, scope to that user's provisioned
        agents; without it (internal tick-loop callers) every managed agent, house
        roster agents included."""
        managed = [vpp for vpp in self.vpps.values() if vpp.is_my_vpp]
        if owner_id is not None:
            managed = [vpp for vpp in managed if vpp.owner_id == owner_id]
        return managed

    def remove_managed_vpp(self, managed_def_id: int) -> bool:
        """Remove a provisioned managed VPP (by its stable DB id): cancel its resting orders and
        drop it from the roster so the tick loop stops driving it. Returns False if not found.
        Call under self._lock to stay race-free with the tick loop and external submits."""
        target = next(
            (v for v in self.vpps.values() if v.managed_def_id == managed_def_id), None
        )
        if target is None:
            return False
        now_sim = self.clock.now_sim()
        now_wall = datetime.now(UTC)
        for order_id in list(target.open_order_ids):
            self.engine.cancel(order_id, sim_ts=now_sim, wall_ts=now_wall)
        self.vpps.pop(target.vpp_id, None)
        log.info("Removed managed VPP id=%s (def=%s)", target.vpp_id, managed_def_id)
        return True

    def _maybe_chat(self, tick_no: int) -> None:
        """On the chat cadence, fire one agent's small-talk LLM call off the tick path —
        round-robin across LLM agents, one in flight at a time, sharing the strategist gate."""
        if self.shared_llm is None or not self.shared_llm.live:
            return
        if tick_no % CHAT_INTERVAL_TICKS != 0:
            return
        if self._chat_task is not None and not self._chat_task.done():
            return
        eligible = [v for v in self.vpps.values() if _agent_chat_client(v.agent) is not None]
        if not eligible:
            return
        vpp = eligible[self._chat_rr % len(eligible)]
        self._chat_rr += 1
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._chat_task = loop.create_task(self._generate_chat(vpp))

    async def _generate_chat(self, vpp: SimulatorVPP) -> None:
        client = _agent_chat_client(vpp.agent)
        if client is None or self.shared_llm is None:
            return
        # Read recent history, then decide reply-vs-fresh: always reply when mentioned by
        # another agent, otherwise a capped random chance, so the room mixes banter with new
        # topics and never becomes an endless reply chain.
        recent = [{"name": m["name"], "text": m["text"]} for m in list(self.chatter)[-6:]]
        mentioned = any(
            vpp.name.lower() in m["text"].lower() for m in recent if m["name"] != vpp.name
        )
        force_fresh = self._chat_reply_streak >= CHAT_MAX_REPLY_STREAK
        reply = bool(recent) and not force_fresh and (mentioned or random.random() < CHAT_REPLY_PROB)
        try:
            messages = build_chat_messages(
                name=vpp.name,
                persona=getattr(vpp.agent, "persona_prompt", None),
                context=self._chat_context(vpp),
                recent_chat=recent,
                reply=reply,
                mentioned=mentioned,
            )
            async with self.shared_llm.gate:
                content = await asyncio.wait_for(
                    client.chat(messages, temperature=0.9, max_tokens=2048),
                    timeout=self.shared_llm.timeout_sec + 30.0,
                )
            text = clean_chat_line(str(content))
            if text:
                self.chatter.append(
                    {"name": vpp.name, "wall_ts": datetime.now(UTC), "text": text}
                )
                self._chat_reply_streak = self._chat_reply_streak + 1 if reply else 0
        except Exception:
            log.warning("chat generation failed for %s", vpp.name)

    def _chat_context(self, vpp: SimulatorVPP) -> dict:
        """Compact social snapshot for chat: recent fills, a small PnL leaderboard, the
        market price, and this agent's own standing."""
        ranked = sorted(self.vpps.values(), key=lambda v: float(v.state.pnl), reverse=True)
        board = [
            {"name": v.name, "pnl": round(float(v.state.pnl), 2)}
            for v in (ranked[:3] + ranked[-2:])
        ]
        last = self.engine.last_price
        return {
            "market_last_price": float(last) if last is not None else None,
            "recent_trades": self._recent_market_trades(limit=6),
            "pnl_leaderboard": board,
            "me": {
                "name": vpp.name,
                "pnl": round(float(vpp.state.pnl), 2),
                "soc": round(vpp.battery.soc_frac, 2),
            },
        }

    # How long a data-source check stays fresh before data_source_status()
    # re-inspects weather coverage (cheap, purely in-memory).
    DATA_SOURCE_TTL_SEC = 60.0

    def refresh_data_sources(self) -> None:
        """Check which data source each built-in VPP is currently using."""
        checked_at = datetime.now(UTC)
        sim_ts = self.clock.now_sim()
        weather_sources: list[dict[str, str]] = []
        for vpp in self.vpps.values():
            # Only report components a VPP actually has — with 30 VPPs the
            # banner would otherwise drown in "no PV configured" noise.
            if vpp.params.pv_kw_peak > 0:
                weather_sources.append(self._pv_source_for(vpp, sim_ts))
            if vpp.wind is not None:
                weather_sources.append(self._wind_source_for(vpp, sim_ts))
        active_real = [s for s in weather_sources if s["status"] == "real"]
        fallback = [s for s in weather_sources if s["status"] == "fallback"]

        if active_real and not fallback:
            weather_summary = "Open-Meteo + pvlib"
        elif active_real and fallback:
            weather_summary = "Mixed PV sources"
        elif fallback:
            weather_summary = "Synthetic PV fallback"
        else:
            weather_summary = "Synthetic profiles"

        external_source = self._external_market_source_for()
        if external_source["status"] == "real":
            price_summary = "CAISO OASIS RTM"
        elif external_source["status"] == "fallback":
            price_summary = "CAISO OASIS DAM"
        elif external_source["status"] == "disabled":
            price_summary = "External market disabled"
        else:
            price_summary = "Synthetic CAISO price"

        self._data_source_status = {
            "checked_at": checked_at,
            "sim_ts": sim_ts,
            "summary": f"{weather_summary} + {price_summary}",
            "sources": [*weather_sources, external_source],
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

    def external_market_quote(self) -> ExternalMarketQuote:
        return self._external_market_quote

    def _external_market_source_for(self) -> dict[str, str]:
        q = self._external_market_quote
        detail = q.detail
        if q.interval_start is not None and q.interval_end is not None:
            detail = (
                f"{detail} Raw LMP {q.raw_lmp} {q.unit}; "
                f"import {q.import_price}, export {q.export_price}; interval "
                f"{q.interval_start.isoformat()} to {q.interval_end.isoformat()}."
            )
        return {
            "component": "CAISO SP15 electricity price",
            "status": q.status,
            "source": q.source,
            "detail": detail,
        }

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
    ) -> ExternalSubmitResult:
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
            intent = decision.accepted[0]
            external_quote = self._external_quote_for_intent(intent)
            result = self.engine.submit(
                vpp_id=vpp_id,
                side=intent.side,
                price=intent.price,
                qty=intent.qty,
                sim_ts=now_sim,
                wall_ts=now_wall,
                ttl_sec=self.order_ttl_sec or None,
                rest_unfilled=external_quote is None,
            )
            self._record_trades(result.trades)
            external_events: list[ExternalTradeEvent] = []
            if external_quote is not None and result.order.remaining_qty > 0:
                # SDK/REST VPPs carry no in-memory state here (see above), so the
                # external fill is only *published* (for the tape/chart/WS) — unlike
                # the built-in path in _submit_intent, it is not applied to a
                # SimulatorVPP. Ownership accounting for these orders lives at the
                # REST layer.
                external_events.append(
                    self._publish_external_trade_for_vpp_id(
                        vpp_id=vpp_id,
                        side=intent.side,
                        qty=result.order.remaining_qty,
                        quote=external_quote,
                        sim_ts=now_sim,
                    )
                )
                result.order.remaining_qty = Decimal("0")
        return {
            "order_id": result.order.order_id,
            "remaining_qty": str(result.order.remaining_qty),
            # Surface the TTL so API integrators know unfilled remainders are
            # swept at this sim time (order.cancelled event) instead of resting.
            "expires_at_sim": result.order.expires_at,
            "trades": [
                *[t.model_dump(mode="json") for t in result.trades],
                *[t.model_dump(mode="json") for t in external_events],
            ],
        }

    async def start(self) -> None:
        if self._task is not None:
            return
        settings = get_settings()
        # CAISO only matters in the real-price market (it is the clearing price). The
        # pure-P2P market neither trades against the grid nor displays it, so skip the
        # poll there to avoid needless external calls.
        if (
            settings.external_market_enabled
            and self.market_mode == "realprice"
            and self._external_market_task is None
        ):
            self._external_market_task = asyncio.create_task(
                self._run_external_market_poll(), name="external-market-loop"
            )
            self._external_market_task.add_done_callback(_log_unexpected_loop_exit)
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
        if self._external_market_task is not None:
            self._external_market_task.cancel()
            await asyncio.gather(self._external_market_task, return_exceptions=True)
            self._external_market_task = None
        if self._ppo_renew_task is not None:
            self._ppo_renew_task.cancel()
            await asyncio.gather(self._ppo_renew_task, return_exceptions=True)
            self._ppo_renew_task = None
        await self._shutdown_reflections()

    async def _run_external_market_poll(self) -> None:
        settings = get_settings()
        poll_sec = max(5.0, settings.external_market_poll_sec)
        while True:
            await self._refresh_external_market_once()
            await asyncio.sleep(poll_sec)

    async def _refresh_external_market_once(self) -> None:
        settings = get_settings()
        if self.external_market_client is None:
            return
        quote = await self.external_market_client.fetch_latest_quote(
            region=settings.market_region,
            node=settings.external_market_node,
            fallback_price=Decimal(str(settings.external_market_fallback_price)),
            transaction_fee=Decimal(str(settings.external_market_transaction_fee)),
        )
        self._external_market_quote = quote
        self._data_source_status = None

    async def _shutdown_reflections(self) -> None:
        """Cancel in-flight LLM guidance/reflection tasks and close the shared client.

        An LLM round-trip can take minutes; without this, shutdown mid-call
        leaves a 'Task was destroyed but it is pending!' warning and the shared
        httpx client's connections leak."""
        pending: list = []
        clients = set()
        for vpp in self.my_managed_vpps():
            task = getattr(vpp.agent, "_reflection_task", None)
            if task is not None and not task.done():
                task.cancel()
                pending.append(task)
            online = getattr(vpp.agent, "_online_task", None)
            if online is not None and not online.done():
                online.cancel()
                pending.append(online)
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
        self._persist_online_weights()

    def _persist_online_weights(self) -> None:
        """Save each live-learning policy's weights to settings.online_learning_save_dir
        (one file per VPP) so a later run resumes from where learning left off. Off by
        default (empty dir); best-effort — a save failure is logged, never fatal."""
        from pathlib import Path

        from eflux.config import PROJECT_ROOT, get_settings

        save_dir = get_settings().online_learning_save_dir
        if not save_dir:
            return
        out = Path(save_dir)
        if not out.is_absolute():
            out = PROJECT_ROOT / out
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.exception("could not create online weight dir %s", out)
            return
        for vpp in self.vpps.values():
            policy = _online_policy_of(vpp.agent)
            if policy is None:
                continue
            try:
                policy.save(str(out / f"{vpp.name}.pt"))
            except Exception:
                log.exception("failed saving online weights for %s", vpp.name)

    # -- renew PPOs (retrain on latest real data + hot-reload) ----------------------------
    def ppo_renew_status(self) -> PpoRenewStatus:
        return dict(self._ppo_renew)

    def start_ppo_renew(self, *, days: int = 30, episodes: int = 40, epochs: int = 300) -> bool:
        """Kick off a background retrain on the latest `days` of real data, then hot-reload
        every live online policy. Returns False if a renew is already in flight."""
        if self._ppo_renew["state"] in ("training", "reloading"):
            return False
        from eflux.config import PROJECT_ROOT

        # Per-market checkpoint: retrain the checkpoint this market's PPO agents loaded, against
        # this market's own structure, so a p2p renew never overwrites the realprice prior.
        checkpoint = str(PROJECT_ROOT / "checkpoints" / f"bc_primitive_{self.market_mode}.pt")
        self._ppo_renew_task = asyncio.create_task(
            self._run_ppo_renew(days=days, episodes=episodes, epochs=epochs, checkpoint_path=checkpoint),
            name="ppo-renew",
        )
        self._ppo_renew_task.add_done_callback(_log_unexpected_loop_exit)
        return True

    async def _run_ppo_renew(self, *, days: int, episodes: int, epochs: int, checkpoint_path: str) -> None:
        self._ppo_renew.update(
            state="training",
            started_at=datetime.now(UTC).isoformat(),
            finished_at=None,
            error=None,
            reloaded=0,
            metrics=None,
            detail=f"training on ~{days}d of real CAISO price + weather",
        )
        try:
            from eflux.agents.ppo.train import run_training

            # Training fetches real data (network) + runs torch — keep it off the event loop.
            metrics = await asyncio.to_thread(
                run_training,
                checkpoint_path,
                real_data=True,
                days=days,
                episodes=episodes,
                epochs=epochs,
                market_mode=self.market_mode,
            )
            self._ppo_renew.update(state="reloading", metrics=metrics, detail="hot-reloading live policies")
            n = await self.reload_online_policies(checkpoint_path)
            self._ppo_renew.update(
                state="done",
                finished_at=datetime.now(UTC).isoformat(),
                reloaded=n,
                detail=f"renewed and reloaded {n} PPO policies",
            )
            log.info("PPO renew complete: reloaded %d policies from %s", n, checkpoint_path)
        except Exception as e:
            log.exception("PPO renew failed")
            self._ppo_renew.update(
                state="error",
                finished_at=datetime.now(UTC).isoformat(),
                error=f"{type(e).__name__}: {e}",
            )

    async def reload_online_policies(self, checkpoint_path: str) -> int:
        """Hot-swap weights into every live online policy — standalone PPO StrategyAgents,
        the PPO mirrors, and the hybrid agents' online executors — under the tick lock so a
        reload can't race a tick."""
        count = 0
        async with self._lock:
            for vpp in self.vpps.values():
                policy = _online_policy_of(vpp.agent)
                reload = getattr(policy, "reload_weights", None)
                if policy is None or not callable(reload):
                    continue
                try:
                    reload(checkpoint_path)
                    count += 1
                except Exception:
                    log.exception("reload weights failed for %s", vpp.name)
        return count

    async def _run(self) -> None:
        log.info("Simulator loop started (speed=%sx, tick=%ss)", self.clock.speed, self.clock.tick_sim_sec)
        tick_h = self.clock.tick_sim_sec / 3600.0
        async for tick_no, sim_ts in self.clock.ticks():
            async with self._lock:
                self._expire_orders(sim_ts)
                snapshot = self.engine.snapshot(depth_levels=5)
                market_snap = MarketSnapshot.from_engine(
                    sim_ts,
                    snapshot,
                    external_market=self._external_market_quote,
                    # Both live markets price freely off own marginal cost — CAISO is a
                    # reference (p2p) / the clearing price (realprice), never a valuation cap.
                    anchor_to_external=False,
                    market_mode=self.market_mode,
                )
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
                        # Only surface a CAISO price when it reflects a live feed
                        # (real/fallback). Synthetic/disabled would otherwise draw a
                        # flat line at the configured fallback and imply a feed.
                        external_price=(
                            self._external_market_quote.raw_lmp
                            if self._external_market_quote.is_real_price
                            else None
                        ),
                        bid_depth=bb.total_qty if bb else Decimal("0"),
                        ask_depth=ba.total_qty if ba else Decimal("0"),
                    )
                )
            self._maybe_chat(tick_no)

    def _recent_market_trades(self, limit: int = 8) -> list[dict]:
        """Latest market-wide fills with party names — prompt context for
        learning agents (who is trading with whom, and at what price)."""
        out: list[dict] = []
        for t in list(self.trade_log)[-limit:]:
            if isinstance(t, ExternalTradeEvent):
                vpp = self.vpps.get(t.vpp_id)
                vpp_name = vpp.name if vpp else f"external-{t.vpp_id}"
                out.append(
                    {
                        "price": float(t.price),
                        "qty": float(t.qty),
                        "buyer": vpp_name if t.side == "buy" else t.counterparty,
                        "seller": t.counterparty if t.side == "buy" else vpp_name,
                        "external": True,
                    }
                )
                continue
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
        """Each LLM agent's latest successful guidance/reflection, for the other LLM
        agents' prompts. Tagged with vpp_id so an agent can drop its own."""
        out: list[dict] = []
        for vpp in self.my_managed_vpps():
            entries = getattr(vpp.agent, "reflection_log", None)
            if not entries:
                continue
            last_ok = next((e for e in reversed(entries) if e.get("ok")), None)
            if last_ok is None:
                continue
            entry = {
                "vpp_id": vpp.vpp_id,
                "name": vpp.name,
                "rationale": last_ok.get("rationale", ""),
            }
            if "price_adjust" in last_ok or "qty_scale" in last_ok:
                entry.update(
                    {
                        "pa": last_ok.get("price_adjust"),
                        "qs": last_ok.get("qty_scale"),
                    }
                )
            else:
                entry.update(
                    {
                        "preferred_modes": last_ok.get("preferred_modes", []),
                        "avoid_modes": last_ok.get("avoid_modes", []),
                        "risk_budget": last_ok.get("risk_budget"),
                        "soc_target": last_ok.get("soc_target"),
                        "execution_style": last_ok.get("execution_style", ""),
                    }
                )
            out.append(entry)
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
            risk_rejections_total=float(self.risk_rejections_by_vpp.get(vpp.vpp_id, 0)),
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

    def market_balance(self) -> MarketBalanceSummary:
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
        if self.market_mode == "realprice":
            self._submit_intent_realprice(vpp, intent, sim_ts)
            return
        # Pure P2P: agents trade only with each other through the CDA. CAISO never
        # settles trades here (it is a reference signal only), so unfilled orders rest.
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
                rest_unfilled=True,
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

    def _submit_intent_realprice(
        self, vpp: SimulatorVPP, intent: OrderIntent, sim_ts: datetime
    ) -> None:
        """Real-time price market: agents are pure price-takers against the live CAISO
        price. An order settles in full against the grid when it crosses the grid's
        import/export quote; otherwise nothing happens (no peer matching, no resting —
        the agent simply re-quotes next tick). Battery (dispatched) orders trade too,
        so battery arbitrage against the grid works. Agent volume never moves the price:
        that is the point — a clean testbed for strategy P&L against a real price curve.
        """
        quote = self._external_quote_for_intent(intent, include_dispatched=True)
        if quote is None:
            return  # no live grid price, or the order does not cross the grid spread
        try:
            self._settle_external_trade(
                vpp,
                side=intent.side,
                qty=intent.qty,
                quote=quote,
                sim_ts=sim_ts,
            )
        except Exception:
            log.exception("VPP %s grid settlement failed", vpp.vpp_id)
            return
        # Mirror the P2P balance debit, but only on an actual fill (nothing rests).
        # Battery-band (dispatched) quotes settle through the battery, not the PV-load
        # imbalance, so they leave the accumulator alone.
        if not intent.dispatched:
            signed = -float(intent.qty) if intent.side == "sell" else float(intent.qty)
            vpp.state.pending_net_kwh += signed

    def _external_quote_for_intent(
        self, intent: OrderIntent, *, include_dispatched: bool = False
    ) -> ExternalMarketQuote | None:
        if intent.dispatched and not include_dispatched:
            return None
        quote = self._external_market_quote
        if not quote.external_trading_enabled:
            return None
        if intent.side == "buy" and intent.price >= quote.import_price:
            return quote
        if intent.side == "sell" and intent.price <= quote.export_price:
            return quote
        return None

    def _settle_external_trade(
        self,
        vpp: SimulatorVPP,
        *,
        side: str,
        qty: Decimal,
        quote: ExternalMarketQuote,
        sim_ts: datetime,
    ) -> ExternalTradeEvent:
        event = self._publish_external_trade_for_vpp_id(
            vpp_id=vpp.vpp_id,
            side=side,
            qty=qty,
            quote=quote,
            sim_ts=sim_ts,
        )
        self._apply_external_trade_to_vpp(event)
        return event

    def _publish_external_trade_for_vpp_id(
        self,
        *,
        vpp_id: int,
        side: str,
        qty: Decimal,
        quote: ExternalMarketQuote,
        sim_ts: datetime,
    ) -> ExternalTradeEvent:
        event = ExternalTradeEvent(
            external_trade_id=self._alloc_external_trade_id(),
            sim_ts=sim_ts,
            wall_ts=datetime.now(UTC),
            vpp_id=vpp_id,
            side=side,
            price=quote.import_price if side == "buy" else quote.export_price,
            raw_lmp=quote.raw_lmp,
            qty=qty,
            region=quote.region,
            node=quote.node,
            counterparty="CAISO SP15",
            interval_start=quote.interval_start,
            interval_end=quote.interval_end,
        )
        self._publish_event(event)
        return event

    def _alloc_external_trade_id(self) -> int:
        tid = self._next_external_trade_id
        self._next_external_trade_id += 1
        return tid

    def _record_trades(self, trades: list[TradeEvent]) -> None:
        for trade in trades:
            self._apply_trade_to_vpp(trade, side="buy")
            self._apply_trade_to_vpp(trade, side="sell")

    def _apply_external_trade_to_vpp(self, trade: ExternalTradeEvent) -> None:
        vpp = self.vpps.get(trade.vpp_id)
        if vpp is None:
            return

        qty_f = float(trade.qty)
        cash = Decimal(str(float(trade.price) * qty_f))
        self._settle_cash_and_energy(vpp, side=trade.side, qty_f=qty_f, cash=cash)

        self._push_recent_trade(
            vpp,
            {
                "trade_id": f"external-{trade.external_trade_id}",
                "kind": trade.kind,
                "side": trade.side,
                "price": str(trade.price),
                "raw_lmp": str(trade.raw_lmp),
                "qty": str(trade.qty),
                "cash": str(cash),
                "counterparty": trade.counterparty,
                "counterparty_vpp_id": 0,
                "buy_vpp_id": trade.vpp_id if trade.side == "buy" else 0,
                "sell_vpp_id": 0 if trade.side == "buy" else trade.vpp_id,
                "sim_ts": trade.sim_ts,
                "wall_ts": trade.wall_ts,
            },
        )

    def _apply_trade_to_vpp(self, trade: TradeEvent, *, side: str) -> None:
        vpp_id = trade.buy_vpp_id if side == "buy" else trade.sell_vpp_id
        vpp = self.vpps.get(vpp_id)
        if vpp is None:
            return

        qty_f = float(trade.qty)
        cash = Decimal(str(float(trade.price) * qty_f))
        counterparty = trade.sell_vpp_id if side == "buy" else trade.buy_vpp_id
        self._settle_cash_and_energy(vpp, side=side, qty_f=qty_f, cash=cash)

        self._push_recent_trade(
            vpp,
            {
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
            },
        )

    def _settle_cash_and_energy(
        self, vpp: SimulatorVPP, *, side: str, qty_f: float, cash: Decimal
    ) -> None:
        """Apply a fill's cash, energy counters, and battery SoC to a VPP. Shared
        by internal (P2P) and external (CAISO) settlement so the two can't drift."""
        tick_h = self._current_tick_h
        if side == "buy":
            vpp.state.pnl -= cash
            vpp.state.cumulative_energy_bought_kwh += qty_f
            vpp.battery.charge(power_kw=qty_f / max(1e-9, tick_h), duration_h=tick_h)
        else:
            vpp.state.pnl += cash
            vpp.state.cumulative_energy_sold_kwh += qty_f
            vpp.battery.discharge(power_kw=qty_f / max(1e-9, tick_h), duration_h=tick_h)

    def _push_recent_trade(self, vpp: SimulatorVPP, record: dict) -> None:
        vpp.trade_count += 1
        vpp.recent_trades.insert(0, record)
        vpp.recent_trades = vpp.recent_trades[:50]
        record_trade = getattr(vpp.agent, "record_trade", None)
        if callable(record_trade):
            record_trade(record)


def _online_policy_of(agent: BaseAgent):
    """The agent's live-learning policy (something with a `.save`), if any: hybrid agents
    hold it as `_executor`, StrategyAgent (incl. the PPO mirror) as `_policy`. Returns None
    for non-online agents — used only for opt-in weight persistence."""
    for attr in ("_executor", "_policy"):
        policy = getattr(agent, attr, None)
        if policy is not None and hasattr(policy, "save"):
            return policy
    return None


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
