"""Market snapshot — REST view of current order book + clock state."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from eflux.api.deps import SimulatorDep

router = APIRouter(prefix="/market", tags=["market"])


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
