"""VPP CRUD for authenticated users (manage their owned VPPs)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from eflux.api.deps import CurrentUser, DbSession, SimulatorDep
from eflux.db.models import VPP
from eflux.market.units import internal_cash_to_usd
from eflux.simulator.agent_spec import validate_vpp_params

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
    # "live" | "degraded" | "offline" — computed from the agent's recent
    # reflection outcomes so the UI badge reflects reality, not startup state.
    llm_health_state: str


class ManagedTradeOut(BaseModel):
    trade_id: int | str
    kind: str | None = None
    side: str
    price: str  # $/MWh
    raw_lmp: str | None = None  # $/MWh
    qty: str
    cash: str  # USD (converted from internal $/MWh x kWh units; see market.units)
    counterparty: str | None = None
    counterparty_vpp_id: int
    buy_vpp_id: int
    sell_vpp_id: int
    sim_ts: datetime
    wall_ts: datetime


class ReflectionEntryOut(BaseModel):
    ts: datetime
    ok: bool
    # Legacy ReflectiveAgent hint fields; null for HybridPolicyAgent strategist logs.
    price_adjust: float | None = None
    qty_scale: float | None = None
    # HybridPolicyAgent + LLMStrategist guidance fields.
    preferred_modes: list[str] | None = None
    avoid_modes: list[str] | None = None
    risk_budget: float | None = None
    soc_target: float | None = None
    execution_style: str | None = None
    rationale: str = ""
    # Durable takeaway the LLM distilled from the latest guidance/reflection cycle.
    # None for entries recorded before lessons existed.
    lesson: str | None = None
    meta_control: dict[str, float] | None = None
    error: str | None


class LLMHealthOut(BaseModel):
    ok_count: int
    fail_count: int
    last_ok_ts: datetime | None
    state: str  # "live" | "degraded" | "offline"


class ManagedVPPPerformanceOut(BaseModel):
    id: int
    name: str
    pnl: str  # USD (converted from internal $/MWh x kWh units; see market.units)
    cumulative_energy_bought_kwh: float
    cumulative_energy_sold_kwh: float
    soc_kwh: float
    soc_frac: float
    recent_trades: list[ManagedTradeOut]
    # LLM guidance/reflection audit trail, newest first. Empty for non-LLM agents.
    reflections: list[ReflectionEntryOut]
    llm_health: LLMHealthOut | None


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


def _llm_health(vpp) -> tuple[str, LLMHealthOut | None]:
    """Derive the runtime LLM health from the agent's reflection counters.

    offline  — no live LLM client configured
    live     — at least one reflection succeeded and the most recent attempt did
    degraded — client configured but reflections are failing (or none succeeded yet)
    """
    agent = vpp.agent
    log_entries = list(getattr(agent, "reflection_log", []))
    ok_count = getattr(agent, "ok_count", 0)
    fail_count = getattr(agent, "fail_count", 0)
    last_ok_ts = getattr(agent, "last_ok_ts", None)

    if not vpp.llm_live:
        state = "offline"
    elif log_entries and log_entries[-1]["ok"]:
        state = "live"
    elif ok_count == 0 and fail_count == 0:
        state = "live"  # configured, no attempt yet — give it the benefit of the doubt
    else:
        state = "degraded"

    health = None
    if vpp.llm_live or ok_count or fail_count:
        health = LLMHealthOut(
            ok_count=ok_count, fail_count=fail_count, last_ok_ts=last_ok_ts, state=state
        )
    return state, health


@router.get("/managed", response_model=list[ManagedVPPOut])
async def list_my_managed_vpps(
    user: CurrentUser,
    sim: SimulatorDep,
) -> list[ManagedVPPOut]:
    out: list[ManagedVPPOut] = []
    for vpp in sim.my_managed_vpps():
        state, health = _llm_health(vpp)
        status = vpp.llm_status
        if health is not None and (health.ok_count or health.fail_count):
            status = f"{status} — {health.ok_count} ok / {health.fail_count} failed"
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
                llm_status=status,
                llm_health_state=state,
            )
        )
    return out


@router.get("/managed/{vpp_id}/performance", response_model=ManagedVPPPerformanceOut)
async def get_my_managed_vpp_performance(
    vpp_id: int,
    user: CurrentUser,
    sim: SimulatorDep,
) -> ManagedVPPPerformanceOut:
    vpp = next((v for v in sim.my_managed_vpps() if v.vpp_id == vpp_id), None)
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "managed VPP not found")
    _, health = _llm_health(vpp)
    reflections = [
        ReflectionEntryOut(**entry)
        for entry in reversed(list(getattr(vpp.agent, "reflection_log", [])))
    ]
    return ManagedVPPPerformanceOut(
        id=vpp.vpp_id,
        name=vpp.name,
        pnl=str(internal_cash_to_usd(vpp.state.pnl)),
        cumulative_energy_bought_kwh=vpp.state.cumulative_energy_bought_kwh,
        cumulative_energy_sold_kwh=vpp.state.cumulative_energy_sold_kwh,
        soc_kwh=vpp.battery.soc_kwh,
        soc_frac=vpp.battery.soc_frac,
        recent_trades=[_managed_trade_out(t) for t in vpp.recent_trades[:25]],
        reflections=reflections,
        llm_health=health,
    )


def _managed_trade_out(record: dict) -> ManagedTradeOut:
    # Trade `price`/`raw_lmp` stay in $/MWh; only the settled `cash` total is
    # converted from internal units to USD for display (see market.units).
    out = {**record, "cash": str(internal_cash_to_usd(Decimal(str(record["cash"]))))}
    return ManagedTradeOut(**out)


@router.post("", response_model=VPPOut, status_code=status.HTTP_201_CREATED)
async def create_vpp(payload: VPPCreate, session: DbSession, user: CurrentUser) -> VPPOut:
    # Same validation path as the built-in roster (simulator/agent_spec.py) —
    # internal and external participants share one params schema.
    # ValueError also covers the unknown-keys rejection; the detail mirrors
    # FastAPI's native 422 shape (a list of {loc, msg, type}) so clients can
    # parse every validation failure on this endpoint the same way.
    try:
        parsed = validate_vpp_params(payload.params)
    except ValidationError as e:
        detail = [
            {
                "loc": ["body", "params", *err["loc"]],
                "msg": err["msg"],
                "type": err["type"],
            }
            for err in e.errors(include_url=False)
        ]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail) from e
    except ValueError as e:
        detail = [{"loc": ["body", "params"], "msg": str(e), "type": "value_error"}]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail) from e
    vpp = VPP(
        owner_id=user.id,
        name=payload.name,
        params=parsed,
        is_external=True,  # user-created → external SDK or UI driver
    )
    session.add(vpp)
    try:
        await session.flush()
    except Exception as e:
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
