"""Simulator runner: drives the matching engine + in-process built-in VPPs.

External (SDK) VPPs use the same engine through the REST/WS API. Concurrent submitters
share an asyncio.Lock to keep the (sync) matching engine race-free.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
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
from eflux.forecasting.service import ForecastService
from eflux.market.clock import RollingClock
from eflux.market.events import EventKind, ExternalTradeEvent, TickEvent, TradeEvent
from eflux.market.matching_engine import MatchingEngine
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad, WindTurbine

if TYPE_CHECKING:
    from eflux.agents.ppo.training_data import RealMarketData
    from eflux.agents.reflective.pool import SharedLLM
    from eflux.forecasting.schema import ForecastBundle

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
    # Chatroom presence (Tier-0 owner preferences): a chat-only voice hint for the LLM,
    # and a display color/emoji surfaced with the agent's messages.
    chat_style: str | None = None
    chat_color: str | None = None
    chat_avatar: str | None = None
    algorithm: str | None = None
    # Whether the LLM strategist is layered on the base algorithm (set by provisioning). Drives
    # the UI's "LLM + <ALGO>" label and gates external-guidance (Tier A3) steering.
    llm_enabled: bool = False
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
        # Latest processed tick number — surfaced as the Agent Protocol tick_id so external
        # agents can detect stale context / order responses against the current market tick.
        self._last_tick_no = 0
        # Durable results: this boot's market_sessions row id (None = durability off, e.g.
        # tests/backtests or DB trouble at startup) + the wall-gated snapshot writer state.
        self.session_id: int | None = None
        self._stats_task: asyncio.Task | None = None
        self._last_stats_wall: float = 0.0
        # "p2p" = peer-to-peer CDA (agents trade each other; CAISO is reference-only).
        # "realprice" = pure price-taking against the live CAISO price (orders settle
        # vs the grid, no peer matching). Selected per launch via EFLUX_MARKET_MODE.
        self.market_mode: str = settings.market_mode
        self.order_ttl_sec: float = settings.order_ttl_sec
        self.imbalance_settlement_enabled = settings.imbalance_settlement_enabled
        self.imbalance_penalty_mult = settings.imbalance_penalty_mult
        self.curtailment_price_per_kwh = settings.curtailment_price_per_kwh
        self.physical_backstop_enabled = settings.physical_backstop_enabled
        self.imbalance_totals_by_vpp: dict[int, dict[str, float]] = {}
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
        self.fallback_invocations_by_vpp: dict[int, int] = {}
        self.veto_holds_by_vpp: dict[int, int] = {}
        self.decide_ticks_by_vpp: dict[int, int] = {}
        # Site-weather memo: several VPPs share coords (e.g. the HKU rooftop),
        # and today/future forecast days are deliberately not disk-cached.
        self._site_weather_cache: dict[tuple[float, float], object] = {}
        self._next_vpp_id = -1  # internal VPPs use negative ids to avoid clashing with DB ids
        self._next_external_trade_id = 1
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._external_market_task: asyncio.Task | None = None
        self.forecast_service: ForecastService | None = None
        self._forecast_task: asyncio.Task | None = None
        self._forecast_bootstrap_task: asyncio.Task | None = None
        self._forecast_real_data: RealMarketData | None = None
        # Live hourly weather frames covering ~now±2d. The warmup archive above ends
        # yesterday by design, so realized/NWP lookups at live timestamps need this
        # forecast-endpoint source or they would silently fall back to constants.
        self._forecast_live_weather: object | None = None  # pv site: ghi / temp_air
        self._forecast_live_wind: object | None = None  # wind site: wind_speed
        # Published CAISO DAM hourly curve (pd.Series) anchoring the price forecasts,
        # and the last realized prices as the anchor's persistence fallback.
        self._forecast_dam_prices: object | None = None
        self._forecast_last_price: dict[str, float] = {}
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
        algorithm: str | None = None,
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
            algorithm=algorithm,
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
                style=vpp.chat_style,
            )
            async with self.shared_llm.gate:
                content = await asyncio.wait_for(
                    client.chat(messages, temperature=0.9, max_tokens=2048),
                    timeout=self.shared_llm.timeout_sec + 30.0,
                )
            text = clean_chat_line(str(content))
            if text:
                self.post_chat(vpp, text, source="agent")
                self._chat_reply_streak = self._chat_reply_streak + 1 if reply else 0
        except Exception:
            log.warning("chat generation failed for %s", vpp.name)

    def post_chat(self, vpp: SimulatorVPP, text: str, *, source: str = "agent") -> dict:
        """Append one chatroom line for a VPP (its LLM, or its owner speaking through it).
        Owner posts land in the same room the LLM agents read, so they can react."""
        entry = {
            "name": vpp.name,
            "wall_ts": datetime.now(UTC),
            "text": text,
            "color": vpp.chat_color,
            "avatar": vpp.chat_avatar,
            "source": source,
        }
        self.chatter.append(entry)
        return entry

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
        """Entry point for a single SDK-submitted order. Honors the realtime-only constraint."""
        if not self.clock.is_realtime:
            raise PermissionError(
                f"external orders rejected: market speed is {self.clock.speed}x (realtime required)"
            )
        async with self._lock:
            return self._submit_one_external(
                vpp_id=vpp_id,
                side=side,
                price=price,
                qty=qty,
                now_sim=self.clock.now_sim(),
                now_wall=datetime.now(UTC),
            )

    async def submit_external_batch(
        self, *, orders: list[dict], cancels: list[int]
    ) -> dict:
        """A batch of external orders + cancels under one lock (Agent Protocol v1). Cancels
        run first, so a cancel-then-replace frees open-order budget; each order is gated
        independently, so one rejection never aborts the batch. Ownership, rate limits, and
        idempotency are enforced by the REST layer before this is called."""
        if not self.clock.is_realtime:
            raise PermissionError(
                f"external orders rejected: market speed is {self.clock.speed}x (realtime required)"
            )
        async with self._lock:
            now_sim = self.clock.now_sim()
            now_wall = datetime.now(UTC)
            cancelled = [
                {"order_id": oid, "ok": bool(self.engine.cancel(oid, sim_ts=now_sim, wall_ts=now_wall))}
                for oid in cancels
            ]
            results: list[dict] = []
            for i, spec in enumerate(orders):
                item: dict = {"index": i, "client_ref": spec.get("client_ref")}
                try:
                    item.update(
                        status="accepted",
                        **self._submit_one_external(
                            vpp_id=spec["vpp_id"],
                            side=spec["side"],
                            price=spec["price"],
                            qty=spec["qty"],
                            now_sim=now_sim,
                            now_wall=now_wall,
                        ),
                    )
                except RiskRejected as e:
                    item.update(
                        status="rejected",
                        reason=e.reason,
                        order_id=None,
                        remaining_qty=None,
                        expires_at_sim=None,
                        trades=[],
                    )
                results.append(item)
            return {"tick_id": self._last_tick_no, "results": results, "cancelled": cancelled}

    def _submit_one_external(
        self, *, vpp_id: int, side: str, price: Decimal, qty: Decimal, now_sim, now_wall
    ) -> ExternalSubmitResult:
        """Risk-gate + submit one external order. The caller must hold self._lock. Raises
        RiskRejected when the gate vetoes the order.

        Same gate as built-in agents (principle #7). External VPPs carry no in-memory
        battery/DER state here, so only the universal static and rate limits apply; ownership
        was already checked at the REST layer."""
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
            # SDK/REST VPPs carry no in-memory state here, so the external fill is only
            # *published* (tape/chart/WS) — not applied to a SimulatorVPP. Ownership
            # accounting for these orders lives at the REST layer.
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
            # Surface the TTL so integrators know unfilled remainders are swept at this
            # sim time (order.cancelled event) instead of resting.
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
        if settings.forecast_enabled and self.forecast_service is None:
            self.forecast_service = ForecastService(nwp=self._forecast_nwp_lookups())
            self._forecast_bootstrap_task = asyncio.create_task(
                self._bootstrap_forecast_service(), name="forecast-bootstrap"
            )
            self._forecast_bootstrap_task.add_done_callback(_log_unexpected_loop_exit)
        if settings.forecast_enabled and self._forecast_task is None:
            self._forecast_task = asyncio.create_task(
                self._run_forecast_refresh(), name="forecast-refresh-loop"
            )
            self._forecast_task.add_done_callback(_log_unexpected_loop_exit)
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
        if self._forecast_task is not None:
            self._forecast_task.cancel()
            await asyncio.gather(self._forecast_task, return_exceptions=True)
            self._forecast_task = None
        if self._forecast_bootstrap_task is not None:
            self._forecast_bootstrap_task.cancel()
            await asyncio.gather(self._forecast_bootstrap_task, return_exceptions=True)
            self._forecast_bootstrap_task = None
        self._save_forecast_state()
        if self._ppo_renew_task is not None:
            self._ppo_renew_task.cancel()
            await asyncio.gather(self._ppo_renew_task, return_exceptions=True)
            self._ppo_renew_task = None
        # Flush a final stat snapshot (after the tick loop is down, so the read is
        # settled) so a restart doesn't lose the last cadence window. Best-effort.
        if self._stats_task is not None:
            await asyncio.gather(self._stats_task, return_exceptions=True)
            self._stats_task = None
        if self.session_id is not None and get_settings().stats_enabled:
            try:
                rows = self._collect_stat_rows(self._last_tick_no, self.clock.now_sim())
                if rows:
                    await self._persist_stat_rows(rows)
            except Exception:
                log.exception("Final stat snapshot failed")
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

    async def _bootstrap_forecast_service(self) -> None:
        settings = get_settings()
        state_path = self._forecast_state_path()
        data: RealMarketData | None = None
        # wait_for cannot kill a to_thread worker; a timed-out fetch keeps running
        # detached and its result is discarded.
        timeout = max(1.0, settings.forecast_bootstrap_timeout_sec)
        try:
            await asyncio.wait_for(asyncio.to_thread(self._load_forecast_live_frames), timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Live weather frames unavailable; forecast weather lookups fall back to archive"
            )
        if settings.forecast_dam_anchor_enabled:
            try:
                await asyncio.wait_for(asyncio.to_thread(self._load_forecast_dam_prices), timeout)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "DAM anchor fetch failed; price anchors fall back to persistence"
                )
        try:
            if state_path.exists():
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._load_forecast_warmup_data,
                            settings.forecast_warmup_days,
                        ),
                        timeout,
                    )
                    self._forecast_real_data = data
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Forecast weather lookups unavailable during state restore")
                self.forecast_service = await asyncio.to_thread(
                    ForecastService.load,
                    state_path,
                    nwp=self._forecast_nwp_lookups(),
                )
                if self.forecast_service.is_warm:
                    log.info("Forecast service restored from %s", state_path)
                    return
                log.warning(
                    "Forecast state at %s has no price observations (e.g. saved after a "
                    "throttled CAISO warm-up); keeping its weather models and re-running "
                    "the price warm-start on top",
                    state_path,
                )
            if data is None:
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._load_forecast_warmup_data, settings.forecast_warmup_days
                        ),
                        timeout,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Fresh CAISO warm-up window unavailable; trying cached windows")
            data = await self._select_forecast_warmup_data(data, timeout)
            if data is None:
                raise RuntimeError("no forecast warm-up data available (fresh or cached)")
            self._forecast_real_data = data
            nwp = self._forecast_nwp_lookups()
            service = self.forecast_service or ForecastService(nwp=nwp)
            service.warm_start(series=self._forecast_warmup_series(data), nwp=nwp)
            self.forecast_service = service
            log.info(
                "Forecast service warm-started from CAISO/weather history %s..%s "
                "(%d price points); price_p2p uses CAISO as a proxy prior",
                data.start.date(),
                data.end.date(),
                0 if data.price is None else len(data.price),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Forecast warm-start skipped; continuing with empty online models")

    async def _select_forecast_warmup_data(
        self, fresh: RealMarketData | None, timeout: float
    ) -> RealMarketData | None:
        """Fresh warm-up window, unless its price series is too thin — then the best
        recent cached window wins. CAISO 429 throttling degrades the fresh fetch to
        partial/empty price data, and the thin result is parquet-cached for the rest
        of the day, so without this fallback one bad fetch poisons every later boot.
        """
        from eflux.agents.ppo.training_data import cached_price_windows

        settings = get_settings()
        min_points = max(0, settings.forecast_warmup_min_price_points)
        best = fresh
        best_points = 0 if fresh is None or fresh.price is None else len(fresh.price)
        if best_points >= min_points:
            return best
        fresh_points = best_points
        for start_d, end_d in cached_price_windows()[:4]:
            if best_points >= min_points:
                break
            try:
                candidate = await asyncio.wait_for(
                    asyncio.to_thread(self._load_forecast_warmup_window, start_d, end_d),
                    timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Cached warm-up window %s..%s unusable", start_d, end_d)
                continue
            points = 0 if candidate.price is None else len(candidate.price)
            if points > best_points:
                best, best_points = candidate, points
        if best is not None and best is not fresh:
            log.warning(
                "CAISO warm-up fetch too thin (%d price points, need %d); "
                "warm-starting from cached window %s..%s (%d points) instead",
                fresh_points,
                min_points,
                best.start.date(),
                best.end.date(),
                best_points,
            )
        return best

    def _load_forecast_warmup_window(self, start_d: date, end_d: date) -> RealMarketData:
        from eflux.agents.ppo.training_data import load_real_market_data

        return load_real_market_data(start_date=start_d, end_date=end_d)

    def _load_forecast_warmup_data(self, days: int) -> RealMarketData:
        from eflux.agents.ppo.training_data import load_real_market_data

        return load_real_market_data(days=days)

    def _forecast_warmup_series(
        self, data: RealMarketData
    ) -> dict[str, Iterable[tuple[datetime, float]]]:
        weather = data.weather
        wind = data.wind
        series: dict[str, Iterable[tuple[datetime, float]]] = {
            "price_real": list(data.price.items()),
            # Proxy prior for Phase A: seed P2P from CAISO until live P2P prints
            # accumulate enough online history of their own.
            "price_p2p": list(data.price.items()),
        }
        if weather is not None and not getattr(weather, "empty", True):
            for col in ("ghi", "temp_air"):
                if col in getattr(weather, "columns", []):
                    series[col] = list(weather[col].items())
        if wind is not None and not getattr(wind, "empty", True):
            if "wind_speed" in getattr(wind, "columns", []):
                series["wind_speed"] = list(wind["wind_speed"].items())
        return series

    async def _run_forecast_refresh(self) -> None:
        settings = get_settings()
        refresh_sec = max(1.0, settings.forecast_refresh_sec)
        while True:
            try:
                self._refresh_forecast_once()
            except Exception:
                log.exception("Forecast refresh failed; continuing with previous bundle")
            await asyncio.sleep(refresh_sec)

    def _refresh_forecast_once(self) -> None:
        # Don't refresh (and above all don't SAVE) until the bootstrap task has
        # restored/warm-started the service: a save against the placeholder service
        # writes an empty state.json which bootstrap would then "restore", silently
        # replacing the warm start with poisoned zero models on every boot.
        bootstrap = self._forecast_bootstrap_task
        if bootstrap is not None and not bootstrap.done():
            return
        service = self.forecast_service
        if service is None:
            return
        sim_ts = self.clock.now_sim()
        quote = self._external_market_quote
        last_price = self.engine.last_price
        real_obs = float(quote.raw_lmp) if quote.is_real_price else None
        p2p_obs = None if last_price is None else float(last_price)
        if real_obs is not None:
            self._forecast_last_price["price_real"] = real_obs
        if p2p_obs is not None:
            self._forecast_last_price["price_p2p"] = p2p_obs
        service.observe(
            sim_ts,
            price_real=real_obs,
            price_p2p=p2p_obs,
            # None (not a 0.0 default) when no data source covers sim_ts, so a data
            # gap is skipped instead of being learned as a realized zero.
            ghi=self._forecast_weather_lookup("ghi", sim_ts),
            temp_air=self._forecast_weather_lookup("temp_air", sim_ts),
            wind_speed=self._forecast_weather_lookup("wind_speed", sim_ts),
        )
        service.refresh(sim_ts)
        self._save_forecast_state()

    def _context_forecast(self) -> ForecastBundle | None:
        """Latest bundle for agent contexts, or None while it is still a placeholder.

        The pre-bootstrap bundle (model_version "empty") and refreshed-but-never-warmed
        services are all zeros; agents that read them numerically (PPO forecast channels,
        price-trend oracles) would treat those zeros as strong bearish signals and stop
        quoting. Exposing "no forecast" instead lets every consumer fall back to its
        neutral path.
        """
        service = self.forecast_service
        if service is None:
            return None
        if not service.is_warm:
            return None
        bundle = service.latest
        if bundle.model_version == "empty":
            return None
        # A restored checkpoint carries the previous session's last bundle; hide
        # it until the live refresh loop produces a current one.
        if (self.clock.now_sim() - bundle.as_of).total_seconds() > 900.0:
            return None
        return bundle

    def _forecast_nwp_lookups(self) -> dict[str, Callable[[datetime], float]]:
        lookups: dict[str, Callable[[datetime], float]] = {
            "ghi": lambda ts: self._forecast_weather_value("ghi", ts, 0.0),
            "temp_air": lambda ts: self._forecast_weather_value("temp_air", ts, 20.0),
            "wind_speed": lambda ts: self._forecast_weather_value("wind_speed", ts, 0.0),
        }
        if get_settings().forecast_dam_anchor_enabled:
            lookups["price_real"] = lambda ts: self._forecast_price_anchor(ts, "price_real")
            lookups["price_p2p"] = lambda ts: self._forecast_price_anchor(ts, "price_p2p")
        return lookups

    def _load_forecast_dam_prices(self) -> None:
        """Fetch the published CAISO DAM hourly curve covering ~now±2d.

        Tomorrow's DAM publishes ~13:00 PT, so once fetched the anchor covers at
        least now..+24h. Cached per (node, window) parquet under the training cache
        with a `dam_` prefix (the warm-up fallback scanner only globs `lmp_`)."""
        import pandas as pd

        from eflux.config import PROJECT_ROOT

        settings = get_settings()
        node = settings.external_market_node
        start_d = date.today() - timedelta(days=1)
        end_d = date.today() + timedelta(days=2)
        cache_dir = PROJECT_ROOT / "data" / "cache" / "training"
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe = node.replace("/", "_")
        cache = cache_dir / f"dam_{safe}_{start_d.isoformat()}_{end_d.isoformat()}.parquet"
        if cache.exists():
            series = pd.read_parquet(cache)["lmp"]
        else:
            start = datetime(start_d.year, start_d.month, start_d.day, tzinfo=UTC)
            end = datetime(end_d.year, end_d.month, end_d.day, tzinfo=UTC)
            rows = CaisoOasisClient().fetch_lmp_history_sync(node=node, start=start, end=end)
            series = pd.Series(
                {
                    r.interval_start.astimezone(UTC).replace(minute=0, second=0, microsecond=0): float(r.price)
                    for r in rows
                }
            ).sort_index()
            # Normalize to a tz-aware DatetimeIndex so hour lookups behave the same
            # as the parquet round-trip path.
            series.index = pd.to_datetime(series.index, utc=True)
            if len(series):
                try:
                    series.rename("lmp").to_frame().to_parquet(cache)
                except Exception:
                    log.exception("DAM anchor cache write failed: %s", cache)
        if len(series):
            self._forecast_dam_prices = series
            log.info(
                "Forecast DAM anchor loaded: %d hourly points %s..%s",
                len(series),
                series.index.min(),
                series.index.max(),
            )
        else:
            log.warning(
                "Forecast DAM anchor unavailable (empty fetch); price anchors fall back to persistence"
            )

    def _forecast_price_anchor(self, ts: datetime, target: str) -> float:
        """DAM price at ts's hour; stale-bounded asof; else last realized; else ref."""
        series = self._forecast_dam_prices
        if series is not None and len(series):  # type: ignore[arg-type]
            hour = ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
            try:
                if hour in series.index:  # type: ignore[attr-defined]
                    return float(series.loc[hour])  # type: ignore[attr-defined]
                past = series.loc[:hour]  # type: ignore[attr-defined]
                if len(past) and (hour - past.index[-1]) <= timedelta(hours=26):
                    return float(past.iloc[-1])
            except Exception:
                pass
        last = self._forecast_last_price.get(target)
        if last is not None:
            return last
        # The market's own last print beats a static constant as a persistence
        # anchor (e.g. price_real in p2p mode, where the CAISO quote may be
        # throttled and real prices are never observed).
        engine_last = self.engine.last_price
        if engine_last is not None:
            return float(engine_last)
        return float(get_settings().external_market_fallback_price)

    def _load_forecast_live_frames(self) -> None:
        """Fetch pv/wind-site hourly weather covering ~now±2d for live lookups.

        load_real_market_data() ends yesterday by design (fully archived), so on its
        own every live-timestamp lookup would miss. Recording those misses as 0.0
        observations is what poisoned the online models (temp_air locked to 0 with a
        -20 5m residual, wind_speed/ghi flat 0) — this frame closes the gap.
        """
        from datetime import date, timedelta

        from eflux.data.weather import fetch_hourly_sync

        settings = get_settings()
        start = date.today() - timedelta(days=2)
        end = date.today() + timedelta(days=2)
        self._forecast_live_weather = fetch_hourly_sync(
            settings.site_default_lat, settings.site_default_lon, start, end
        )
        self._forecast_live_wind = fetch_hourly_sync(
            settings.site_wind_lat, settings.site_wind_lon, start, end
        )

    @staticmethod
    def _hourly_frame_value(frame: object, col: str, ts: datetime) -> float | None:
        """Exact-hour lookup in an Open-Meteo hourly DataFrame; None when absent."""
        if frame is None or getattr(frame, "empty", True) or col not in getattr(frame, "columns", []):
            return None
        target = ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        try:
            if target in frame.index:  # type: ignore[attr-defined]
                value = float(frame.loc[target, col])  # type: ignore[attr-defined]
                return value if value == value else None
        except Exception:
            return None
        return None

    def _forecast_weather_lookup(self, name: str, ts: datetime) -> float | None:
        """Realized/NWP weather at `ts`, or None when no data source covers it."""
        frame = self._forecast_live_wind if name == "wind_speed" else self._forecast_live_weather
        value = self._hourly_frame_value(frame, name, ts)
        if value is not None:
            return value
        data = self._forecast_real_data
        if data is None:
            return None
        nan = float("nan")
        if name == "ghi":
            value = data.ghi_at(ts, nan)
        elif name == "wind_speed":
            value = data.wind_speed_at(ts, nan)
        elif name == "temp_air":
            value = data._weather_field(data.weather, "temp_air", ts, nan)
        else:
            return None
        return value if value == value else None

    def _forecast_weather_value(self, name: str, ts: datetime, default: float) -> float:
        value = self._forecast_weather_lookup(name, ts)
        return default if value is None else value

    def _forecast_state_path(self) -> Path:
        from eflux.config import PROJECT_ROOT

        path = Path(get_settings().forecast_state_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path / "state.json"

    def _save_forecast_state(self) -> None:
        service = self.forecast_service
        if service is None or service.observation_count() == 0:
            return
        try:
            state_path = self._forecast_state_path()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            service.save(state_path)
        except Exception:
            log.exception("Forecast state save failed")

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
            self._last_tick_no = tick_no
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
            self._maybe_snapshot_stats(tick_no, sim_ts)

    def _maybe_snapshot_stats(self, tick_no: int, sim_ts: datetime) -> None:
        """On the wall-clock cadence, persist a per-agent stat snapshot off the tick path.

        Collection is synchronous (no awaits) so the rows are tick-coherent without
        holding self._lock; the DB write runs in a spawned task. At most one write is
        in flight — if the previous one hasn't finished, this sample is dropped, never
        queued (drop-don't-block, same shape as _maybe_chat)."""
        if self.session_id is None:
            return
        settings = get_settings()
        if not settings.stats_enabled:
            return
        now = time.monotonic()
        if self._last_stats_wall and now - self._last_stats_wall < settings.stats_snapshot_sec:
            return
        if self._stats_task is not None and not self._stats_task.done():
            return
        self._last_stats_wall = now
        rows = self._collect_stat_rows(tick_no, sim_ts)
        if not rows:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._stats_task = loop.create_task(self._persist_stat_rows(rows))

    def _collect_stat_rows(self, tick_no: int, sim_ts: datetime) -> list[dict]:
        """One stat dict per live VPP — the durable leaderboard sample. Synchronous by
        design: it runs between awaits on the event loop, so it reads a tick-coherent
        view of every agent without taking self._lock."""
        from eflux.market.units import internal_cash_to_usd
        from eflux.stats.categories import agent_category, is_llm_vpp

        wall_ts = datetime.now(UTC)
        rows: list[dict] = []
        for vpp in self.vpps.values():
            strategist = getattr(vpp.agent, "strategist", None)
            client = getattr(strategist, "client", None) if strategist is not None else None
            p = vpp.params
            rows.append(
                {
                    "session_id": self.session_id,
                    "vpp_id": vpp.vpp_id,
                    "name": vpp.name,
                    "managed_def_id": vpp.managed_def_id,
                    "owner_id": vpp.owner_id,
                    "strategy": vpp.strategy,
                    "category": agent_category(vpp),
                    "is_llm": is_llm_vpp(vpp),
                    "llm_model": getattr(client, "model", None),
                    "tick_no": tick_no,
                    "sim_ts": sim_ts,
                    "wall_ts": wall_ts,
                    "pnl_usd": internal_cash_to_usd(vpp.state.pnl),
                    "soc_kwh": vpp.battery.soc_kwh,
                    "soc_frac": vpp.battery.soc_frac,
                    "energy_bought_kwh": vpp.state.cumulative_energy_bought_kwh,
                    "energy_sold_kwh": vpp.state.cumulative_energy_sold_kwh,
                    "trade_count": vpp.trade_count,
                    "pv_kw_peak": p.pv_kw_peak,
                    "wind_kw_rated": p.wind_kw_rated,
                    "battery_kwh": p.battery_kwh,
                    "battery_kw_max": p.battery_kw_max,
                    "load_kw_base": p.load_kw_base,
                    "gas_kw_max": p.gas_kw_max,
                }
            )
        return rows

    async def _persist_stat_rows(self, rows: list[dict]) -> None:
        """Batched snapshot insert. Best-effort: DB trouble is logged and dropped —
        durability is an optional feature and must never destabilize the market loop."""
        from eflux.db.models import VppStatSnapshot
        from eflux.db.session import get_sessionmaker

        try:
            async with get_sessionmaker()() as session:
                session.add_all(VppStatSnapshot(**row) for row in rows)
                await session.commit()
        except Exception:
            log.exception("Stat-snapshot persist failed (%d rows dropped)", len(rows))

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
        Lets agents avoid re-quoting the same forced position while scarcity
        pricing can still see resting demand depth. One book walk per tick;
        depth is TTL-bounded."""
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
        # Route this tick's DER balance through the physical buffer first. Only the
        # unbuffered overflow/shortfall becomes a forced market position.
        cap = max(vpp.params.battery_kwh, 1.0)
        gen_kwh = vpp.state.net_kw * tick_h
        max_rate_kwh = max(0.0, vpp.battery.max_power_kw * tick_h)
        if gen_kwh >= 0.0:
            absorbed = min(gen_kwh, max(0.0, vpp.battery.capacity_kwh - vpp.battery.soc_kwh), max_rate_kwh)
            vpp.battery.apply_kwh(absorbed)
            vpp.state.pending_net_kwh += gen_kwh - absorbed
        else:
            needed = -gen_kwh
            supplied = min(needed, max(0.0, vpp.battery.soc_kwh), max_rate_kwh)
            vpp.battery.apply_kwh(-supplied)
            vpp.state.pending_net_kwh -= needed - supplied
        vpp.state.soc_kwh = vpp.battery.soc_kwh
        unclamped_pending = vpp.state.pending_net_kwh
        clamped_pending = min(cap, max(-cap, unclamped_pending))
        overflow_kwh = unclamped_pending - clamped_pending
        vpp.state.pending_net_kwh = clamped_pending
        self._settle_imbalance_overflow(vpp, overflow_kwh)
        self._submit_physical_backstop(vpp, sim_ts)

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
            forecast=self._context_forecast(),
        )
        self.decide_ticks_by_vpp[vpp.vpp_id] = self.decide_ticks_by_vpp.get(vpp.vpp_id, 0) + 1
        intents = vpp.agent.decide(ctx)
        self._gate_and_submit(vpp, ctx, intents, sim_ts, open_order_count)

    def _settle_imbalance_overflow(self, vpp: SimulatorVPP, overflow_kwh: float) -> None:
        if not self.imbalance_settlement_enabled or abs(overflow_kwh) <= 1e-12:
            return
        totals = self.imbalance_totals_by_vpp.setdefault(
            vpp.vpp_id,
            {
                "unserved_load_kwh": 0.0,
                "spilled_generation_kwh": 0.0,
                "settlement_cash": 0.0,
            },
        )
        if overflow_kwh < 0.0:
            unserved_kwh = -overflow_kwh
            import_price = float(
                getattr(self._external_market_quote, "import_price", self._external_fallback_price())
            )
            penalty_price = self.imbalance_penalty_mult * import_price
            cash = unserved_kwh * penalty_price
            vpp.state.pnl -= Decimal(str(cash))
            totals["unserved_load_kwh"] += unserved_kwh
            totals["settlement_cash"] -= cash
            log.debug(
                "VPP %s imbalance unserved %.6f kWh settled at %.6f",
                vpp.vpp_id,
                unserved_kwh,
                penalty_price,
            )
        else:
            spilled_kwh = overflow_kwh
            cash = spilled_kwh * self.curtailment_price_per_kwh
            vpp.state.pnl += Decimal(str(cash))
            totals["spilled_generation_kwh"] += spilled_kwh
            totals["settlement_cash"] += cash
            log.debug(
                "VPP %s imbalance spill %.6f kWh settled at %.6f",
                vpp.vpp_id,
                spilled_kwh,
                self.curtailment_price_per_kwh,
            )

    def _external_fallback_price(self) -> float:
        return float(get_settings().external_market_fallback_price)

    def _submit_physical_backstop(self, vpp: SimulatorVPP, sim_ts: datetime) -> None:
        if (
            self.market_mode != "realprice"
            or not self.physical_backstop_enabled
            or not self._external_market_quote.external_trading_enabled
        ):
            return
        pending = vpp.state.pending_net_kwh
        if vpp.battery.soc_frac >= 0.995 and pending > 0.0:
            qty = pending
            side = "sell"
            price = self._external_market_quote.export_price
        elif vpp.battery.soc_frac <= 0.005 and pending < 0.0:
            qty = -pending
            side = "buy"
            price = self._external_market_quote.import_price
        else:
            return
        if qty < 0.01:
            return
        log.info(
            "VPP %s physical backstop %s %.6f kWh at %s",
            vpp.vpp_id,
            side,
            qty,
            price,
        )
        self._submit_intent(
            vpp,
            OrderIntent(side=side, price=price, qty=Decimal(str(qty))),
            sim_ts,
        )

    def imbalance_totals(self, vpp_id: int) -> dict[str, float]:
        totals = self.imbalance_totals_by_vpp.get(vpp_id)
        if totals is None:
            return {
                "unserved_load_kwh": 0.0,
                "spilled_generation_kwh": 0.0,
                "settlement_cash": 0.0,
            }
        return dict(totals)

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
        fallback's (re-gated) action is submitted instead; otherwise the agent stands
        down for the tick and the hold is counted."""
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
                self.fallback_invocations_by_vpp[vpp.vpp_id] = (
                    self.fallback_invocations_by_vpp.get(vpp.vpp_id, 0) + 1
                )
                decision = self.risk_gate.validate(
                    fallback.decide(ctx),
                    vpp_id=vpp.vpp_id,
                    params=vpp.params,
                    battery=vpp.battery,
                    tick_h=ctx.tick_duration_h,
                    open_order_count=open_order_count,
                )
                self._record_rejections(vpp.vpp_id, len(decision.rejected))
            else:
                self.veto_holds_by_vpp[vpp.vpp_id] = self.veto_holds_by_vpp.get(vpp.vpp_id, 0) + 1
        for intent in decision.accepted:
            self._submit_intent(vpp, intent, sim_ts)

    def _record_rejections(self, vpp_id: int, n: int) -> None:
        if n:
            self.risk_rejections += n
            self.risk_rejections_by_vpp[vpp_id] = self.risk_rejections_by_vpp.get(vpp_id, 0) + n

    def _expire_orders(self, sim_ts: datetime) -> None:
        """Expire TTL'd resting orders and shed local open-order ids."""
        expired = self.engine.expire(sim_ts=sim_ts, wall_ts=datetime.now(UTC))
        for order in expired:
            vpp = self.vpps.get(order.vpp_id)
            if vpp is None:
                continue  # external order — the owner re-quotes on its own
            if order.order_id in vpp.open_order_ids:
                vpp.open_order_ids.remove(order.order_id)

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
        if side == "buy":
            vpp.state.pnl -= cash
            vpp.state.cumulative_energy_bought_kwh += qty_f
            cover = min(qty_f, max(0.0, -vpp.state.pending_net_kwh))
            vpp.state.pending_net_kwh += cover
            vpp.battery.apply_kwh(qty_f - cover)
            vpp.state.soc_kwh = vpp.battery.soc_kwh
        else:
            vpp.state.pnl += cash
            vpp.state.cumulative_energy_sold_kwh += qty_f
            clear = min(qty_f, max(0.0, vpp.state.pending_net_kwh))
            vpp.state.pending_net_kwh -= clear
            vpp.battery.apply_kwh(-(qty_f - clear))
            vpp.state.soc_kwh = vpp.battery.soc_kwh

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
