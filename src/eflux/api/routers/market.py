"""Market snapshot — REST view of current order book + clock state."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from eflux.api.deps import CurrentUser, DbSession, SimulatorDep
from eflux.data.electricity_market import ExternalMarketQuote
from eflux.db.models import VPP
from eflux.market.events import ExternalTradeEvent, TradeEvent
from eflux.market.units import internal_cash_to_usd
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


class MarketSnapshot(BaseModel):
    sim_ts: datetime
    speed: float
    best_bid: str | None
    best_ask: str | None
    last_price: str | None
    bids: list[tuple[str, str]]
    asks: list[tuple[str, str]]
    num_builtin_vpps: int
    data_source: DataSourceStatus
    external_market: ExternalMarketOut
    balance: MarketBalanceOut


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


@router.get("/snapshot", response_model=MarketSnapshot)
async def snapshot(sim: SimulatorDep, depth: int = 10) -> MarketSnapshot:
    # async + sim._lock: book reads must not interleave with the tick loop's
    # matching/expiry. As a sync route this ran on a threadpool thread, where
    # the asyncio lock offers no protection — a level deleted mid-walk turned
    # into IndexError 500s.
    async with sim._lock:
        s = sim.engine.snapshot(depth_levels=depth)
        balance = sim.market_balance()
    return MarketSnapshot(
        sim_ts=sim.clock.now_sim(),
        speed=sim.clock.speed,
        best_bid=s["best_bid"],
        best_ask=s["best_ask"],
        last_price=s["last_price"],
        bids=s["bids"],
        asks=s["asks"],
        num_builtin_vpps=len(sim.vpps),
        data_source=sim.data_source_status(),
        external_market=_external_market_out(sim.external_market_quote()),
        balance=balance,
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
async def supply_curve(sim: SimulatorDep) -> SupplyCurveOut:
    """Resting orders with per-VPP attribution, best price first on each side.

    This is the merit-order view: walking the asks cheapest-first shows which
    generation category sets the price at each cumulative quantity.
    """

    def walk(side: str) -> list[SupplyCurveOrder]:
        out: list[SupplyCurveOrder] = []
        for o in sim.engine.book.iter_orders(side):
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

    # Walking the book's deques must not interleave with matching/expiry —
    # async + the sim lock keeps this on the event loop, race-free.
    async with sim._lock:
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
    # Endowment (static)
    pv_kw_peak: float
    wind_kw_rated: float
    battery_kwh: float
    battery_kw_max: float
    load_kw_base: float
    gas_kw_max: float
    gas_cost_per_kwh: float
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
    fallback_count: int = 0
    veto_hold_count: int = 0
    risk_rejections: int = 0
    decide_ticks: int = 0
    guidance_change_rate: float | None = None
    mode_override_rate: float | None = None
    avg_price_dev_bps: float | None = None


@router.get("/agents", response_model=list[AgentOut])
async def market_agents(sim: SimulatorDep) -> list[AgentOut]:
    """Live roster of every built-in VPP: who they are, what they own, and how
    they are doing right now. Public — this is the market's cast of characters.

    async (event loop, not threadpool) so each agent's row is a consistent
    tick-coherent read instead of mixing fields from two ticks."""
    from eflux.api.routers.vpps import _llm_health

    out: list[AgentOut] = []
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
                pv_kw_peak=p.pv_kw_peak,
                wind_kw_rated=p.wind_kw_rated,
                battery_kwh=p.battery_kwh,
                battery_kw_max=p.battery_kw_max,
                load_kw_base=p.load_kw_base,
                gas_kw_max=p.gas_kw_max,
                gas_cost_per_kwh=p.gas_cost_per_kwh,
                pnl=str(internal_cash_to_usd(vpp.state.pnl)),
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


class SpeedUpdate(BaseModel):
    speed: float


class SpeedStatusOut(BaseModel):
    speed: float
    is_realtime: bool


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


@router.post("/speed", response_model=SpeedStatusOut)
async def set_market_speed(
    payload: SpeedUpdate,
    user: CurrentUser,
    sim: SimulatorDep,
) -> SpeedStatusOut:
    try:
        sim.clock.set_speed(payload.speed)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return SpeedStatusOut(speed=sim.clock.speed, is_realtime=sim.clock.is_realtime)


@router.post("/ppo/renew", response_model=PpoRenewStartOut)
async def renew_ppos(user: CurrentUser, sim: SimulatorDep, days: int = 30) -> PpoRenewStartOut:
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
