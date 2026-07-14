"""Market snapshot — REST view of current order book + clock state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from eflux.api.deps import AdminUser, DbSession, SimulatorDep
from eflux.data.electricity_market import ExternalMarketQuote
from eflux.db.models import VPP
from eflux.market.events import ExternalTradeEvent, TickEvent, TradeEvent
from eflux.stats.categories import agent_category, is_llm_vpp

router = APIRouter(prefix="/market", tags=["market"])


class ParticipantOut(BaseModel):
    id: int
    name: str
    kind: str  # "builtin" | "external"
    strategy: str | None = None


class DataSourceEntry(BaseModel):
    component: str
    status: str
    source: str
    detail: str


class DataSourceStatus(BaseModel):
    checked_at: datetime
    sim_ts: datetime
    summary: str
    sources: list[DataSourceEntry]


class ExternalMarketOut(BaseModel):
    region: str
    node: str
    raw_lmp: str
    p2p_anchor_price: str
    import_price: str
    export_price: str
    interval_start: datetime | None
    interval_end: datetime | None
    currency: str
    unit: str
    status: str
    source: str
    detail: str
    fetched_at: datetime


class MarketBalanceOut(BaseModel):
    """Live aggregate supply vs demand — the market-consistency instrument."""

    renewable_kw: float
    load_kw: float
    gas_capacity_kw: float
    net_kw: float
    supply_demand_ratio: float | None  # (renewables + gas capacity) / load
    bid_depth_kwh: float
    ask_depth_kwh: float


class MarketSessionOut(BaseModel):
    market_mode: str
    sim_time: datetime
    wall_time: datetime


class MarketSnapshot(BaseModel):
    sim_ts: datetime
    speed: float
    best_bid: str | None
    best_ask: str | None
    last_price: str | None
    bids: list[tuple[str, str]]
    asks: list[tuple[str, str]]
    num_builtin_vpps: int
    data_provenance: Literal["real", "cached", "synthetic"]
    session: MarketSessionOut
    data_source: DataSourceStatus
    external_market: ExternalMarketOut
    balance: MarketBalanceOut
    product_id: str
    delivery_start: datetime
    delivery_end: datetime
    gate_closure: datetime


class DeliveryProductOut(BaseModel):
    product_id: str
    market: str
    delivery_start: datetime
    delivery_end: datetime
    gate_closure: datetime
    opens_at: datetime
    is_open: bool
    is_closed: bool
    best_bid: str | None
    best_ask: str | None
    last_price: str | None


@router.get("/products", response_model=list[DeliveryProductOut])
async def products(sim: SimulatorDep) -> list[DeliveryProductOut]:
    sim_ts = sim.clock.now_sim()
    async with sim._lock:
        visible = sim._ensure_products(sim_ts)
        out: list[DeliveryProductOut] = []
        for product in visible:
            snapshot = sim.engine.snapshot(product.interval_id, depth_levels=1)
            out.append(
                DeliveryProductOut(
                    product_id=product.interval_id,
                    market=product.market,
                    delivery_start=product.start,
                    delivery_end=product.end,
                    gate_closure=product.gate_closure,
                    opens_at=product.opens_at,
                    is_open=product.is_trading_open(sim_ts),
                    is_closed=snapshot["is_closed"],
                    best_bid=snapshot["best_bid"],
                    best_ask=snapshot["best_ask"],
                    last_price=snapshot["last_price"],
                )
            )
        return out


@router.get("/participants", response_model=list[ParticipantOut])
async def participants(sim: SimulatorDep, session: DbSession) -> list[ParticipantOut]:
    """id → name directory for everyone who can appear in the trade tape, so the
    UI can label parties instead of showing raw (negative) internal ids."""
    out = [
        ParticipantOut(id=vpp.vpp_id, name=vpp.name, kind="builtin", strategy=vpp.strategy)
        for vpp in sim.vpps.values()
    ]
    rows = (await session.execute(select(VPP).where(VPP.is_active.is_(True)))).scalars().all()
    out.extend(ParticipantOut(id=v.id, name=v.name, kind="external") for v in rows)
    return out


@router.get("/trades", response_model=list[TradeEvent | ExternalTradeEvent])
def recent_trades(sim: SimulatorDep, limit: int = 200) -> list[TradeEvent | ExternalTradeEvent]:
    """Most recent trades, oldest first — lets clients backfill chart/tape on load."""
    limit = max(1, min(limit, 500))
    log = list(sim.trade_log)
    return log[-limit:]


@router.get("/ticks", response_model=list[TickEvent])
async def recent_ticks(sim: SimulatorDep, limit: int = 100_000) -> list[TickEvent]:
    """Current-session tick history, oldest first, for chart recovery after refresh.

    This is deliberately independent of the default one-hour chart viewport: clients
    receive the complete retained session history and choose their own visible window.
    """
    limit = max(1, min(limit, 100_000))
    async with sim._lock:
        log = list(sim.tick_log)
    return log[-limit:]


@router.get("/snapshot", response_model=MarketSnapshot)
async def snapshot(
    sim: SimulatorDep, depth: int = 10, product_id: str | None = None
) -> MarketSnapshot:
    # async + sim._lock: book reads must not interleave with the tick loop's
    # matching/expiry. As a sync route this ran on a threadpool thread, where
    # the asyncio lock offers no protection — a level deleted mid-walk turned
    # into IndexError 500s.
    async with sim._lock:
        visible = sim._ensure_products(sim.clock.now_sim())
        try:
            product = visible[0] if product_id is None else sim.engine.interval(product_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        s = sim.engine.snapshot(product.interval_id, depth_levels=depth)
        balance = sim.market_balance()
    sim_time = sim.clock.now_sim()
    quote = sim.external_market_quote()
    return MarketSnapshot(
        sim_ts=sim_time,
        speed=sim.clock.speed,
        best_bid=s["best_bid"],
        best_ask=s["best_ask"],
        last_price=s["last_price"],
        bids=s["bids"],
        asks=s["asks"],
        num_builtin_vpps=len(sim.vpps),
        data_provenance=_data_provenance(quote),
        session=MarketSessionOut(
            market_mode=sim.market_mode,
            sim_time=sim_time,
            wall_time=datetime.now(UTC),
        ),
        data_source=sim.data_source_status(),
        external_market=_external_market_out(quote),
        balance=balance,
        product_id=product.interval_id,
        delivery_start=product.start,
        delivery_end=product.end,
        gate_closure=product.gate_closure,
    )


def _external_market_out(q: ExternalMarketQuote) -> ExternalMarketOut:
    return ExternalMarketOut(
        region=q.region,
        node=q.node,
        raw_lmp=str(q.raw_lmp),
        p2p_anchor_price=str(q.p2p_anchor_price),
        import_price=str(q.import_price),
        export_price=str(q.export_price),
        interval_start=q.interval_start,
        interval_end=q.interval_end,
        currency=q.currency,
        unit=q.unit,
        status=q.status,
        source=q.source,
        detail=q.detail,
        fetched_at=q.fetched_at,
    )


def _data_provenance(q: ExternalMarketQuote) -> Literal["real", "cached", "synthetic"]:
    if q.status == "real":
        return "real"
    if q.is_real_price:
        return "cached"
    return "synthetic"


class SupplyCurveOrder(BaseModel):
    price: str
    qty: str  # remaining (unfilled) quantity
    category: str  # solar | wind | gas | battery_load | llm | external
    vpp_name: str | None


class SupplyCurveOut(BaseModel):
    sim_ts: datetime
    asks: list[SupplyCurveOrder]  # best (cheapest) first — the merit order
    bids: list[SupplyCurveOrder]  # best (highest) first — the demand curve


@router.get("/supply_curve", response_model=SupplyCurveOut)
async def supply_curve(sim: SimulatorDep, product_id: str | None = None) -> SupplyCurveOut:
    """Resting orders with per-VPP attribution, best price first on each side.

    This is the merit-order view: walking the asks cheapest-first shows which
    generation category sets the price at each cumulative quantity.
    """

    # Product registration and walking the book's deques must not interleave
    # with matching/expiry.  Keep the complete snapshot under one lock.
    async with sim._lock:
        product = sim._ensure_products(sim.clock.now_sim())[0]
        if product_id is not None:
            try:
                product = sim.engine.interval(product_id)
            except KeyError as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

        def walk(side: str) -> list[SupplyCurveOrder]:
            out: list[SupplyCurveOrder] = []
            for o in sim.engine.iter_orders(product.interval_id, side):
                vpp = sim.vpps.get(o.vpp_id)
                out.append(
                    SupplyCurveOrder(
                        price=str(o.price),
                        qty=str(o.remaining_qty),
                        category=agent_category(vpp) if vpp else "external",
                        vpp_name=vpp.name if vpp else None,
                    )
                )
            return out

        return SupplyCurveOut(sim_ts=sim.clock.now_sim(), asks=walk("sell"), bids=walk("buy"))


class AgentOut(BaseModel):
    id: int
    name: str
    strategy: str
    category: str
    is_llm: bool
    mirror_of: str | None = None
    llm_health_state: str | None  # only for LLM-managed agents
    llm_model: str | None = None  # the strategist's model (LLM agents only) — arena display
    # Behavioural classification is intentionally separate from owned assets.
    archetype: str
    resources: list[str]
    # Endowment (static)
    pv_kw_peak: float
    wind_kw_rated: float
    battery_kwh: float
    battery_kw_max: float
    load_kw_base: float
    gas_kw_max: float
    gas_cost_per_mwh: float
    # Live state (changes every tick)
    pnl: str  # USD (converted from internal $/MWh x kWh units; see market.units)
    soc_kwh: float
    soc_frac: float
    pv_kw: float
    wind_kw: float
    load_kw: float
    net_kw: float
    energy_bought_kwh: float
    energy_sold_kwh: float
    trade_count: int
    recent_trade_count: int
    observation_min: float
    fallback_count: int = 0
    veto_hold_count: int = 0
    risk_rejections: int = 0
    decide_ticks: int = 0
    guidance_change_rate: float | None = None
    mode_override_rate: float | None = None
    avg_price_dev_bps: float | None = None


def _market_agents_out(sim) -> list[AgentOut]:
    """Build one tick-coherent roster snapshot for participants and the Arena."""
    from eflux.agents.character import derive_character, endowment_resources
    from eflux.api.routers.vpps import _llm_health

    out: list[AgentOut] = []
    now_sim = sim.clock.now_sim()
    for vpp in sim.vpps.values():
        health_state: str | None = None
        if vpp.is_my_vpp:
            health_state, _ = _llm_health(vpp)
        strategist = getattr(vpp.agent, "strategist", None)
        client = getattr(strategist, "client", None) if strategist is not None else None
        influence_stats = getattr(vpp.agent, "influence_stats", None)
        if influence_stats is None:
            influence_stats = {}
        p = vpp.params
        out.append(
            AgentOut(
                id=vpp.vpp_id,
                name=vpp.name,
                strategy=vpp.strategy,
                category=agent_category(vpp),
                is_llm=is_llm_vpp(vpp),
                mirror_of=vpp.mirror_of,
                llm_health_state=health_state,
                llm_model=getattr(client, "model", None),
                archetype=derive_character(p).archetype,
                resources=endowment_resources(p),
                pv_kw_peak=p.pv_kw_peak,
                wind_kw_rated=p.wind_kw_rated,
                battery_kwh=p.battery_kwh,
                battery_kw_max=p.battery_kw_max,
                load_kw_base=p.load_kw_base,
                gas_kw_max=p.gas_kw_max,
                gas_cost_per_mwh=p.gas_cost_per_mwh,
                pnl=str(vpp.state.pnl),
                soc_kwh=vpp.battery.soc_kwh,
                soc_frac=vpp.battery.soc_frac,
                pv_kw=vpp.state.pv_kw,
                wind_kw=vpp.state.wind_kw,
                load_kw=vpp.state.load_kw,
                net_kw=vpp.state.net_kw,
                energy_bought_kwh=vpp.state.cumulative_energy_bought_kwh,
                energy_sold_kwh=vpp.state.cumulative_energy_sold_kwh,
                trade_count=vpp.trade_count,
                recent_trade_count=len(vpp.recent_trades),
                observation_min=max(
                    0.0,
                    (now_sim - (vpp.observed_since_sim or vpp.state.sim_ts)).total_seconds() / 60.0,
                ),
                fallback_count=sim.fallback_invocations_by_vpp.get(vpp.vpp_id, 0),
                veto_hold_count=sim.veto_holds_by_vpp.get(vpp.vpp_id, 0),
                risk_rejections=sim.risk_rejections_by_vpp.get(vpp.vpp_id, 0),
                decide_ticks=sim.decide_ticks_by_vpp.get(vpp.vpp_id, 0),
                guidance_change_rate=influence_stats.get("guidance_change_rate"),
                mode_override_rate=influence_stats.get("mode_override_rate"),
                avg_price_dev_bps=influence_stats.get("avg_price_dev_bps"),
            )
        )
    return out


@router.get("/agents", response_model=list[AgentOut])
async def market_agents(sim: SimulatorDep) -> list[AgentOut]:
    """Live roster of every built-in VPP: who they are, what they own, and how
    they are doing right now. Public — this is the market's cast of characters.

    async (event loop, not threadpool) so each agent's row is a consistent
    tick-coherent read instead of mixing fields from two ticks."""
    return _market_agents_out(sim)


class ArenaOut(BaseModel):
    """Evidence thresholds and contestants used by the model Arena."""

    min_trades: int
    min_observation_min: int
    agents: list[AgentOut]


@router.get("/arena", response_model=ArenaOut)
async def arena(sim: SimulatorDep) -> ArenaOut:
    """Arena-specific roster payload with the live evidence contract exposed."""
    from eflux.config import get_settings

    settings = get_settings()
    return ArenaOut(
        min_trades=settings.arena_min_trades,
        min_observation_min=settings.arena_min_observation_min,
        agents=_market_agents_out(sim),
    )


class MarketReflectionOut(BaseModel):
    vpp_id: int
    vpp_name: str
    health_state: str  # "live" | "degraded" | "offline"
    ts: datetime
    ok: bool
    # Legacy ReflectiveAgent hint fields; null for HybridPolicyAgent strategist logs.
    price_adjust: float | None = None
    qty_scale: float | None = None
    # HybridPolicyAgent + LLMStrategist guidance fields.
    preferred_modes: list[str] | None = None
    avoid_modes: list[str] | None = None
    mode_pin: str | None = None
    risk_budget: float | None = None
    price_bias_bps: float | None = None
    soc_target: float | None = None
    execution_style: str | None = None
    rationale: str = ""
    # NOTE: `lesson` is deliberately NOT exposed here — it's a private, owner-only
    # takeaway surfaced via /vpps/managed/{id}/performance. Extra keys in the spread
    # entry (incl. "lesson") are ignored by pydantic, so it never leaks to this public feed.
    meta_control: dict[str, float] | None = None
    error: str | None


@router.get("/reflections", response_model=list[MarketReflectionOut])
def market_reflections(sim: SimulatorDep, limit: int = 20) -> list[MarketReflectionOut]:
    """LLM guidance feed across all managed agents, newest first. Public so the
    Market page can show what the LLM-steered agent is thinking without a login."""
    from eflux.api.routers.vpps import _llm_health

    limit = max(1, min(limit, 100))
    out: list[MarketReflectionOut] = []
    for vpp in sim.my_managed_vpps():
        state, _ = _llm_health(vpp)
        # list() snapshots the deque atomically — this sync route runs on a
        # threadpool thread while the event loop appends new reflections.
        for entry in list(getattr(vpp.agent, "reflection_log", [])):
            out.append(
                MarketReflectionOut(
                    vpp_id=vpp.vpp_id, vpp_name=vpp.name, health_state=state, **entry
                )
            )
    out.sort(key=lambda r: r.ts, reverse=True)
    return out[:limit]


class ChatMessageOut(BaseModel):
    name: str
    wall_ts: datetime
    text: str
    # Presence extras (older entries predate them): owner-picked display color/emoji,
    # and whether the line came from the agent's LLM or its owner typing.
    color: str | None = None
    avatar: str | None = None
    source: str = "agent"


@router.get("/chatter", response_model=list[ChatMessageOut])
def market_chatter(sim: SimulatorDep, limit: int = 40) -> list[ChatMessageOut]:
    """Agent chatroom — recent casual messages from the LLM-steered agents (and owners
    speaking through them), newest first. Public; only name, timestamp, message, and
    display presence are exposed (no strategy/PnL leakage)."""
    limit = max(1, min(limit, 100))
    msgs = list(sim.chatter)[-limit:]
    return [
        ChatMessageOut(
            name=m["name"],
            wall_ts=m["wall_ts"],
            text=m["text"],
            color=m.get("color"),
            avatar=m.get("avatar"),
            source=m.get("source", "agent"),
        )
        for m in reversed(msgs)
    ]


class PpoRenewStatusOut(BaseModel):
    state: str
    started_at: str | None
    finished_at: str | None
    detail: str
    reloaded: int
    error: str | None
    metrics: dict[str, object] | None


class PpoRenewStartOut(PpoRenewStatusOut):
    status: str


@router.post("/ppo/renew", response_model=PpoRenewStartOut)
async def renew_ppos(user: AdminUser, sim: SimulatorDep, days: int = 30) -> PpoRenewStartOut:
    """Retrain the PPO warm-start on the latest `days` of real CAISO price + weather, then
    hot-reload every live online policy (standalone PPOs, mirrors, and hybrid executors).
    Runs in the background — poll GET /market/ppo/status for progress. Auth-gated like /speed."""
    if not sim.start_ppo_renew(days=max(1, min(days, 60))):
        raise HTTPException(status.HTTP_409_CONFLICT, "a PPO renew is already running")
    return PpoRenewStartOut(status="started", **sim.ppo_renew_status())


@router.get("/ppo/status", response_model=PpoRenewStatusOut)
async def ppo_renew_status(sim: SimulatorDep) -> PpoRenewStatusOut:
    """Current state of the background PPO renew (idle | training | reloading | done | error)."""
    return PpoRenewStatusOut(**sim.ppo_renew_status())
