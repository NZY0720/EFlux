"""Order submission/cancel — external (user-driven) orders flow through here."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from eflux.api.deps import CurrentUser, DbSession, SimulatorDep
from eflux.db.models import VPP

router = APIRouter(prefix="/orders", tags=["orders"])


class OrderSubmit(BaseModel):
    vpp_id: int
    side: Literal["buy", "sell"]
    # Bounds keep a fat-fingered UI/SDK order from distorting the demo market:
    # price is capped well above the gas merit-order top (~72), qty at a level
    # no single battery in the roster could physically deliver.
    price: Decimal = Field(gt=0, le=1000, decimal_places=4)
    qty: Decimal = Field(ge=Decimal("0.01"), le=1000, decimal_places=4)


class TradeOut(BaseModel):
    trade_id: int
    price: str
    qty: str
    buy_vpp_id: int
    sell_vpp_id: int


class OrderSubmitResponse(BaseModel):
    order_id: int
    remaining_qty: str
    # Sim time when the unfilled remainder is swept from the book (order TTL;
    # an `order.cancelled` event is published). None = rests until filled/cancelled.
    expires_at_sim: datetime | None = None
    trades: list[dict]


@router.post("", response_model=OrderSubmitResponse)
async def submit_order(
    payload: OrderSubmit,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> OrderSubmitResponse:
    # Verify user owns the VPP.
    stmt = select(VPP).where(
        VPP.id == payload.vpp_id, VPP.owner_id == user.id, VPP.is_active.is_(True)
    )
    vpp = (await session.execute(stmt)).scalar_one_or_none()
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "VPP not found or not yours")

    try:
        result = await sim.submit_external(
            vpp_id=vpp.id, side=payload.side, price=payload.price, qty=payload.qty
        )
    except PermissionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return OrderSubmitResponse(**result)


class OrderCancel(BaseModel):
    order_id: int


@router.post("/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_order(
    payload: OrderCancel,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> None:
    from datetime import UTC

    # Only the owner may cancel: resolve the resting order's VPP and check it
    # belongs to this user. Built-in VPPs use negative ids and are never
    # user-cancellable. 404 either way — don't leak which orders exist.
    order = sim.engine.book.get(payload.order_id)
    if order is None or order.vpp_id < 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    stmt = select(VPP).where(VPP.id == order.vpp_id, VPP.owner_id == user.id)
    vpp = (await session.execute(stmt)).scalar_one_or_none()
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")

    now_sim = sim.clock.now_sim()
    now_wall = datetime.now(UTC)
    async with sim._lock:
        ok = sim.engine.cancel(payload.order_id, sim_ts=now_sim, wall_ts=now_wall)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    return None
