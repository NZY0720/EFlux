"""Order submission/cancel — external (user-driven) orders flow through here."""

from __future__ import annotations

import time
from collections import OrderedDict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from eflux.agents.hybrid import RiskRejected
from eflux.api.deps import CurrentUser, DbSession, SimulatorDep
from eflux.db.models import VPP
from eflux.market.events import ExternalTradeEvent, TradeEvent

router = APIRouter(prefix="/orders", tags=["orders"])


class OrderSubmit(BaseModel):
    vpp_id: int
    side: Literal["buy", "sell"]
    # Bounds keep a fat-fingered UI/SDK order from distorting the demo market:
    # price is capped well above the gas merit-order top (~72), qty at a level
    # no single battery in the roster could physically deliver.
    price: Decimal = Field(gt=0, le=1000, decimal_places=4)
    qty: Decimal = Field(ge=Decimal("0.01"), le=1000, decimal_places=4)


class OrderSubmitResponse(BaseModel):
    order_id: int
    remaining_qty: str
    # Sim time when the unfilled remainder is swept from the book (order TTL;
    # an `order.cancelled` event is published). None = rests until filled/cancelled.
    expires_at_sim: datetime | None = None
    trades: list[TradeEvent | ExternalTradeEvent]


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
    except RiskRejected as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"risk gate: {e.reason}") from e
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


# --- Agent Protocol v1: batch orders, state read, and per-account governance (Tier A1) ---


class OpenOrderOut(BaseModel):
    order_id: int
    vpp_id: int
    side: str
    price: str
    remaining_qty: str
    expires_at_sim: datetime | None = None


@router.get("/open", response_model=list[OpenOrderOut])
async def open_orders(
    vpp_id: int,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> list[OpenOrderOut]:
    """A VPP's resting orders, so an async agent can reconcile its book state without scraping
    the whole market. Ownership enforced."""
    stmt = select(VPP).where(VPP.id == vpp_id, VPP.owner_id == user.id, VPP.is_active.is_(True))
    vpp = (await session.execute(stmt)).scalar_one_or_none()
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "VPP not found or not yours")
    return [
        OpenOrderOut(
            order_id=o.order_id,
            vpp_id=o.vpp_id,
            side=o.side,
            price=str(o.price),
            remaining_qty=str(o.remaining_qty),
            expires_at_sim=o.expires_at,
        )
        for o in sim.engine.open_orders_for_vpp(vpp_id)
    ]


class BatchOrderItem(BaseModel):
    vpp_id: int
    side: Literal["buy", "sell"]
    price: Decimal = Field(gt=0, le=1000, decimal_places=4)
    qty: Decimal = Field(ge=Decimal("0.01"), le=1000, decimal_places=4)
    # Optional caller tag echoed back in the matching result (ordering isn't guaranteed).
    client_ref: str | None = Field(default=None, max_length=64)


class OrderBatch(BaseModel):
    protocol_version: int = 1
    # A repeated key returns the original response instead of re-submitting (retry-safe).
    idempotency_key: str | None = Field(default=None, max_length=128)
    # Late-response policy: a batch whose usefulness window has passed is rejected.
    deadline: datetime | None = None
    orders: list[BatchOrderItem] = Field(default_factory=list, max_length=50)
    cancels: list[int] = Field(default_factory=list, max_length=50)


class BatchOrderResult(BaseModel):
    index: int
    client_ref: str | None = None
    status: str  # "accepted" | "rejected"
    order_id: int | None = None
    remaining_qty: str | None = None
    expires_at_sim: datetime | None = None
    reason: str | None = None
    trades: list[dict] = Field(default_factory=list)


class BatchCancelResult(BaseModel):
    order_id: int
    ok: bool


class BatchResult(BaseModel):
    protocol_version: int
    tick_id: int
    results: list[BatchOrderResult]
    cancelled: list[BatchCancelResult]
    rate_limit_remaining: int


# Per-account order rate limit (token bucket): burst capacity + sustained refill rate.
_RATE_CAPACITY = 120
_RATE_REFILL_PER_SEC = 2.0


class _TokenBucket:
    __slots__ = ("tokens", "last")

    def __init__(self) -> None:
        self.tokens = float(_RATE_CAPACITY)
        self.last = time.monotonic()

    def take(self, n: int) -> tuple[bool, int]:
        now = time.monotonic()
        self.tokens = min(_RATE_CAPACITY, self.tokens + (now - self.last) * _RATE_REFILL_PER_SEC)
        self.last = now
        if self.tokens >= n:
            self.tokens -= n
            return True, int(self.tokens)
        return False, int(self.tokens)


_buckets: dict[int, _TokenBucket] = {}


def _rate_check(user_id: int, cost: int) -> tuple[bool, int]:
    bucket = _buckets.get(user_id) or _buckets.setdefault(user_id, _TokenBucket())
    return bucket.take(cost)


# Idempotency: a repeated key from the same account returns the original response instead of
# re-submitting. In-memory, LRU-bounded — consistent with the ephemeral market.
_IDEMPOTENCY_MAX = 2000
_idempotency: OrderedDict[tuple[int, str], BatchResult] = OrderedDict()


def _idem_get(user_id: int, key: str) -> BatchResult | None:
    hit = _idempotency.get((user_id, key))
    if hit is not None:
        _idempotency.move_to_end((user_id, key))
    return hit


def _idem_put(user_id: int, key: str, response: BatchResult) -> None:
    _idempotency[(user_id, key)] = response
    _idempotency.move_to_end((user_id, key))
    while len(_idempotency) > _IDEMPOTENCY_MAX:
        _idempotency.popitem(last=False)


@router.post("/batch", response_model=BatchResult)
async def submit_batch(
    payload: OrderBatch,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> BatchResult:
    """Agent Protocol v1 — submit and cancel a batch of orders in one authenticated call.

    The envelope carries a protocol_version, an optional idempotency_key (a replay returns the
    original result rather than re-submitting), and an optional deadline (a stale batch is
    rejected). Every order's vpp_id must be owned by the caller; cancels only ever touch the
    caller's own resting orders. Each order is risk-gated independently, so one rejection
    doesn't abort the batch. Per-account rate limited (429)."""
    if payload.protocol_version != 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unsupported protocol_version {payload.protocol_version} (this server speaks v1)",
        )
    if not payload.orders and not payload.cancels:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty batch")

    # Idempotency replay — original result, no re-submit, no rate-limit charge.
    if payload.idempotency_key is not None:
        cached = _idem_get(user.id, payload.idempotency_key)
        if cached is not None:
            return cached

    # Late-response policy.
    if payload.deadline is not None:
        deadline = (
            payload.deadline if payload.deadline.tzinfo else payload.deadline.replace(tzinfo=UTC)
        )
        if deadline < datetime.now(UTC):
            raise HTTPException(status.HTTP_409_CONFLICT, "batch deadline has passed")

    # Ownership: every order's vpp must belong to the caller.
    owned = set(
        (await session.execute(select(VPP.id).where(VPP.owner_id == user.id, VPP.is_active.is_(True))))
        .scalars()
        .all()
    )
    for o in payload.orders:
        if o.vpp_id not in owned:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"vpp {o.vpp_id} not found or not yours")

    # Rate limit: each order costs a token; cancels are free.
    allowed, remaining = _rate_check(user.id, len(payload.orders))
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"order rate limit exceeded — {remaining} tokens left, refills at {_RATE_REFILL_PER_SEC}/s",
        )

    # Cancels only touch the caller's own resting orders; others report ok=false.
    auth_cancels: list[int] = []
    denied_cancels: list[BatchCancelResult] = []
    for oid in payload.cancels:
        order = sim.engine.book.get(oid)
        if order is not None and order.vpp_id in owned:
            auth_cancels.append(oid)
        else:
            denied_cancels.append(BatchCancelResult(order_id=oid, ok=False))

    try:
        outcome = await sim.submit_external_batch(
            orders=[o.model_dump() for o in payload.orders], cancels=auth_cancels
        )
    except PermissionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    result = BatchResult(
        protocol_version=1,
        tick_id=outcome["tick_id"],
        results=[BatchOrderResult(**r) for r in outcome["results"]],
        cancelled=[BatchCancelResult(**c) for c in outcome["cancelled"]] + denied_cancels,
        rate_limit_remaining=remaining,
    )
    if payload.idempotency_key is not None:
        _idem_put(user.id, payload.idempotency_key, result)
    return result
