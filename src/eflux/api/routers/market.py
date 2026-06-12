"""Market snapshot — REST view of current order book + clock state."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from eflux.api.deps import CurrentUser, DbSession, SimulatorDep
from eflux.db.models import VPP
from eflux.market.events import TradeEvent
from eflux.simulator.runner import SimulatorVPP

router = APIRouter(prefix="/market", tags=["market"])


def agent_category(vpp: SimulatorVPP) -> str:
    """Coarse merit-order bucket for a built-in VPP, derived from its endowment.

    Checked in merit-order priority: a dedicated gas peaker or wind farm is
    classified by its generator even if it also carries a small battery.
    """
    if vpp.is_my_vpp:
        return "llm"
    p = vpp.params
    if p.gas_kw_max > 0:
        return "gas"
    if p.wind_kw_rated > 0:
        return "wind"
    if p.pv_kw_peak >= 2.0:
        return "solar"
    return "battery_load"


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


@router.get("/trades", response_model=list[TradeEvent])
def recent_trades(sim: SimulatorDep, limit: int = 200) -> list[TradeEvent]:
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
        balance=balance,
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
    llm_health_state: str | None  # only for LLM-managed agents
    # Endowment (static)
    pv_kw_peak: float
    wind_kw_rated: float
    battery_kwh: float
    battery_kw_max: float
    load_kw_base: float
    gas_kw_max: float
    gas_cost_per_kwh: float
    # Live state (changes every tick)
    pnl: str
    soc_kwh: float
    soc_frac: float
    pv_kw: float
    wind_kw: float
    load_kw: float
    net_kw: float
    energy_bought_kwh: float
    energy_sold_kwh: float
    recent_trade_count: int


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
        p = vpp.params
        out.append(
            AgentOut(
                id=vpp.vpp_id,
                name=vpp.name,
                strategy=vpp.strategy,
                category=agent_category(vpp),
                is_llm=vpp.is_my_vpp,
                llm_health_state=health_state,
                pv_kw_peak=p.pv_kw_peak,
                wind_kw_rated=p.wind_kw_rated,
                battery_kwh=p.battery_kwh,
                battery_kw_max=p.battery_kw_max,
                load_kw_base=p.load_kw_base,
                gas_kw_max=p.gas_kw_max,
                gas_cost_per_kwh=p.gas_cost_per_kwh,
                pnl=str(vpp.state.pnl),
                soc_kwh=vpp.battery.soc_kwh,
                soc_frac=vpp.battery.soc_frac,
                pv_kw=vpp.state.pv_kw,
                wind_kw=vpp.state.wind_kw,
                load_kw=vpp.state.load_kw,
                net_kw=vpp.state.net_kw,
                energy_bought_kwh=vpp.state.cumulative_energy_bought_kwh,
                energy_sold_kwh=vpp.state.cumulative_energy_sold_kwh,
                recent_trade_count=len(vpp.recent_trades),
            )
        )
    return out


class MarketReflectionOut(BaseModel):
    vpp_id: int
    vpp_name: str
    health_state: str  # "live" | "degraded" | "offline"
    ts: datetime
    ok: bool
    price_adjust: float
    qty_scale: float
    rationale: str
    # Persisted takeaway the LLM distilled from its hint→outcome history.
    lesson: str | None = None
    error: str | None


@router.get("/reflections", response_model=list[MarketReflectionOut])
def market_reflections(sim: SimulatorDep, limit: int = 20) -> list[MarketReflectionOut]:
    """LLM reflection feed across all managed agents, newest first. Public so the
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


class SpeedUpdate(BaseModel):
    speed: float


@router.post("/speed")
async def set_market_speed(
    payload: SpeedUpdate,
    user: CurrentUser,
    sim: SimulatorDep,
) -> dict:
    try:
        sim.clock.set_speed(payload.speed)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return {"speed": sim.clock.speed, "is_realtime": sim.clock.is_realtime}
