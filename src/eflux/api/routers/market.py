"""Market snapshot — REST view of current order book + clock state."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from eflux.api.deps import DbSession, SimulatorDep
from eflux.db.models import VPP
from eflux.market.events import TradeEvent

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
def snapshot(sim: SimulatorDep, depth: int = 10) -> MarketSnapshot:
    s = sim.engine.snapshot(depth_levels=depth)
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
    )
