"""VPP CRUD for authenticated users (manage their owned VPPs)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from eflux.api.deps import CurrentUser, DbSession, SimulatorDep
from eflux.db.models import VPP
from eflux.vpp.base import VPPParams

router = APIRouter(prefix="/vpps", tags=["vpps"])


class VPPCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    params: dict = Field(default_factory=dict)


class VPPOut(BaseModel):
    id: int
    name: str
    params: dict
    is_active: bool
    is_external: bool
    created_at: datetime


class ManagedVPPOut(BaseModel):
    id: int
    name: str
    params: dict
    is_active: bool
    is_external: bool
    agent_kind: str
    strategy: str
    llm_live: bool
    llm_status: str


class ManagedTradeOut(BaseModel):
    trade_id: int
    side: str
    price: str
    qty: str
    cash: str
    counterparty_vpp_id: int
    buy_vpp_id: int
    sell_vpp_id: int
    sim_ts: datetime
    wall_ts: datetime


class ManagedVPPPerformanceOut(BaseModel):
    id: int
    name: str
    pnl: str
    cumulative_energy_bought_kwh: float
    cumulative_energy_sold_kwh: float
    soc_kwh: float
    soc_frac: float
    recent_trades: list[ManagedTradeOut]


@router.get("", response_model=list[VPPOut])
async def list_my_vpps(session: DbSession, user: CurrentUser) -> list[VPPOut]:
    stmt = select(VPP).where(VPP.owner_id == user.id).order_by(VPP.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [
        VPPOut(
            id=v.id,
            name=v.name,
            params=v.params,
            is_active=v.is_active,
            is_external=v.is_external,
            created_at=v.created_at,
        )
        for v in rows
    ]


@router.get("/managed", response_model=list[ManagedVPPOut])
async def list_my_managed_vpps(
    user: CurrentUser,  # noqa: ARG001 — route belongs to the authenticated My VPPs view
    sim: SimulatorDep,
) -> list[ManagedVPPOut]:
    out: list[ManagedVPPOut] = []
    for vpp in sim.my_managed_vpps():
        out.append(
            ManagedVPPOut(
                id=vpp.vpp_id,
                name=vpp.name,
                params=vpp.params.to_dict(),
                is_active=True,
                is_external=False,
                agent_kind=vpp.agent.__class__.__name__,
                strategy=vpp.strategy,
                llm_live=vpp.llm_live,
                llm_status=vpp.llm_status,
            )
        )
    return out


@router.get("/managed/{vpp_id}/performance", response_model=ManagedVPPPerformanceOut)
async def get_my_managed_vpp_performance(
    vpp_id: int,
    user: CurrentUser,  # noqa: ARG001 — route belongs to the authenticated My VPPs view
    sim: SimulatorDep,
) -> ManagedVPPPerformanceOut:
    vpp = next((v for v in sim.my_managed_vpps() if v.vpp_id == vpp_id), None)
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "managed VPP not found")
    return ManagedVPPPerformanceOut(
        id=vpp.vpp_id,
        name=vpp.name,
        pnl=str(vpp.state.pnl),
        cumulative_energy_bought_kwh=vpp.state.cumulative_energy_bought_kwh,
        cumulative_energy_sold_kwh=vpp.state.cumulative_energy_sold_kwh,
        soc_kwh=vpp.battery.soc_kwh,
        soc_frac=vpp.battery.soc_frac,
        recent_trades=[ManagedTradeOut(**t) for t in vpp.recent_trades[:25]],
    )


@router.post("", response_model=VPPOut, status_code=status.HTTP_201_CREATED)
async def create_vpp(payload: VPPCreate, session: DbSession, user: CurrentUser) -> VPPOut:
    # Validate params shape by round-tripping through VPPParams.
    parsed = VPPParams.from_dict(payload.params).to_dict()
    vpp = VPP(
        owner_id=user.id,
        name=payload.name,
        params=parsed,
        is_external=True,  # user-created → external SDK or UI driver
    )
    session.add(vpp)
    try:
        await session.flush()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_409_CONFLICT, f"name conflict: {e}") from e
    return VPPOut(
        id=vpp.id,
        name=vpp.name,
        params=vpp.params,
        is_active=vpp.is_active,
        is_external=vpp.is_external,
        created_at=vpp.created_at,
    )


@router.delete("/{vpp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_vpp(vpp_id: int, session: DbSession, user: CurrentUser) -> None:
    stmt = select(VPP).where(VPP.id == vpp_id, VPP.owner_id == user.id)
    vpp = (await session.execute(stmt)).scalar_one_or_none()
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "VPP not found")
    vpp.is_active = False
    return None
