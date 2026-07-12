"""Leaderboard — durable, endowment-normalized rankings across market sessions.

Reads the vpp_stat_snapshots written by the simulator (see eflux.stats): per-session
rankings use each identity's last snapshot in that session (PnL is per-boot cumulative,
so the session-final row IS the session result); all-time aggregates session finals
across boots of the same market_mode. Identity across restarts: built-in roster agents
by ``name``, user-provisioned managed agents by ``managed_def_id``.

Public by design — the board is the product's front window. Raw PnL is shown alongside
score v1 (see eflux.stats.score); no owner emails are ever exposed.
"""

from __future__ import annotations

import re
import time
import weakref
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from eflux.api.deps import DbSession, SimulatorDep
from eflux.db.models import MarketSession, VppStatSnapshot
from eflux.evaluation.arb_iq import oracle_arb_profit, realized_arb_profit, spread_capture
from eflux.market.ledger import LedgerCategory
from eflux.stats.score import revenue_scale_usd

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

_ENDOWMENT_FIELDS = (
    "pv_kw_peak",
    "wind_kw_rated",
    "battery_kwh",
    "battery_kw_max",
    "load_kw_base",
    "gas_kw_max",
)
_ARB_CACHE_TTL_SEC = 60.0
_TRADE_DETAIL_RE = re.compile(r"^(buy|sell) ([^ ]+) kWh @ ([^ ]+) USD/MWh$")
_arb_cache: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class SessionOut(BaseModel):
    id: int
    market_mode: str
    started_at: datetime
    ended_at: datetime | None
    price_ref: str
    is_current: bool


class LeaderboardRow(BaseModel):
    identity: str  # "name:<name>" | "managed:<def_id>" — stable across restarts
    name: str
    managed_def_id: int | None
    category: str
    strategy: str
    is_llm: bool
    llm_model: str | None
    pnl_usd: str
    score: float  # score v1 — endowment- and duration-normalized (stats/score.py)
    spread_capture: float | None
    realized_arb_profit: float | None
    oracle_arb_profit: float | None
    trade_count: int
    energy_bought_kwh: float
    energy_sold_kwh: float
    soc_frac: float
    sessions_count: int
    hours: float  # observed sim-hours backing the score
    last_seen_at: datetime


class LeaderboardOut(BaseModel):
    scope: Literal["session", "alltime"]
    session_id: int | None
    market_mode: str
    rows: list[LeaderboardRow]


class EquityPoint(BaseModel):
    tick_no: int
    sim_ts: datetime
    wall_ts: datetime
    pnl_usd: str
    soc_frac: float


class HistoryOut(BaseModel):
    identity: str
    session_id: int
    points: list[EquityPoint]


def _as_utc(ts: datetime | str) -> datetime:
    # SQLite round-trips may drop tzinfo (and aggregate labels can come back as raw
    # ISO strings); snapshots are always written in UTC.
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _identity(name: str, managed_def_id: int | None) -> str:
    return f"managed:{managed_def_id}" if managed_def_id is not None else f"name:{name}"


def _elapsed_h(first: datetime, last: datetime) -> float:
    return max(0.0, (_as_utc(last) - _as_utc(first)).total_seconds() / 3600.0)


async def _live_arb_metrics(sim) -> dict[str, dict[str, float | None]]:
    """Compute trailing-24h metrics from live matching-engine and ledger state."""
    cached = _arb_cache.get(sim)
    now_mono = time.monotonic()
    if cached is not None and now_mono - cached[0] < _ARB_CACHE_TTL_SEC:
        return cached[1]

    async with sim._lock:
        now_sim = sim.clock.now_sim()
        cutoff = now_sim - timedelta(hours=24)
        priced_intervals = [
            (interval, sim.engine.last_price(interval.interval_id))
            for interval in sim.engine.intervals
            if interval.market == sim.market_mode and interval.start >= cutoff
        ]
        prices = [float(price) for _, price in priced_intervals if price is not None]
        durations = [
            interval.duration_h for interval, price in priced_intervals if price is not None
        ]
        entries = [
            entry
            for entry in sim.gateway.ledger.entries
            if entry.category == LedgerCategory.TRADE and entry.occurred_at >= cutoff
        ]
        specs = [
            (
                _identity(vpp.name, vpp.managed_def_id),
                vpp.vpp_id,
                vpp.params.battery_kwh,
                vpp.params.battery_kw_max,
                vpp.params.battery_eta_rt,
                vpp.params.battery_initial_soc_frac,
            )
            for vpp in sim.vpps.values()
        ]

    trades_by_vpp: dict[int, list[dict[str, float | str]]] = {}
    for entry in entries:
        match = _TRADE_DETAIL_RE.fullmatch(entry.detail)
        if match is None:
            continue
        side, qty, price = match.groups()
        trades_by_vpp.setdefault(entry.participant_id, []).append(
            {"side": side, "qty": float(qty), "price": float(price)}
        )

    interval_h = median(durations) if durations else None
    result: dict[str, dict[str, float | None]] = {}
    for identity, vpp_id, battery_kwh, battery_kw_max, eta_rt, initial_soc in specs:
        if battery_kwh <= 0 or battery_kw_max <= 0:
            result[identity] = {
                "spread_capture": None,
                "realized_arb_profit": None,
                "oracle_arb_profit": None,
            }
            continue
        realized = realized_arb_profit(trades_by_vpp.get(vpp_id, ()))
        oracle = (
            oracle_arb_profit(
                prices,
                battery_kwh=battery_kwh,
                battery_kw_max=battery_kw_max,
                interval_h=interval_h,
                round_trip_eff=eta_rt,
                start_soc=initial_soc,
            )
            if prices and interval_h is not None
            else None
        )
        result[identity] = {
            "spread_capture": spread_capture(realized, oracle),
            "realized_arb_profit": realized,
            "oracle_arb_profit": oracle,
        }

    _arb_cache[sim] = (now_mono, result)
    return result


async def _session_finals(
    db, session_ids: list[int]
) -> list[tuple[VppStatSnapshot, float]]:
    """Each (session, identity)'s final snapshot row, paired with the identity's
    observed sim-hours in that session."""
    if not session_ids:
        return []
    agg = (
        select(
            func.max(VppStatSnapshot.id).label("last_id"),
            func.min(VppStatSnapshot.sim_ts).label("first_sim_ts"),
            func.max(VppStatSnapshot.sim_ts).label("last_sim_ts"),
        )
        .where(VppStatSnapshot.session_id.in_(session_ids))
        .group_by(
            VppStatSnapshot.session_id, VppStatSnapshot.name, VppStatSnapshot.managed_def_id
        )
    )
    groups = (await db.execute(agg)).all()
    if not groups:
        return []
    finals = {
        row.id: row
        for row in (
            (
                await db.execute(
                    select(VppStatSnapshot).where(
                        VppStatSnapshot.id.in_([g.last_id for g in groups])
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    out: list[tuple[VppStatSnapshot, float]] = []
    for g in groups:
        snap = finals.get(g.last_id)
        if snap is not None:
            out.append((snap, _elapsed_h(g.first_sim_ts, g.last_sim_ts)))
    return out


def _endowment(snap: VppStatSnapshot) -> dict[str, float]:
    return {field: getattr(snap, field) for field in _ENDOWMENT_FIELDS}


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(db: DbSession, sim: SimulatorDep) -> list[SessionOut]:
    rows = (
        (await db.execute(select(MarketSession).order_by(MarketSession.id.desc())))
        .scalars()
        .all()
    )
    return [
        SessionOut(
            id=r.id,
            market_mode=r.market_mode,
            started_at=r.started_at,
            ended_at=r.ended_at,
            price_ref=str(r.price_ref),
            is_current=(r.id == sim.session_id),
        )
        for r in rows
    ]


@router.get("", response_model=LeaderboardOut)
async def leaderboard(
    db: DbSession,
    sim: SimulatorDep,
    scope: Literal["session", "alltime"] = "session",
    session_id: int | None = None,
    category: str | None = None,
) -> LeaderboardOut:
    """Ranked market results. scope=session ranks one boot (default: the running one);
    scope=alltime aggregates every session of this market_mode across restarts."""
    if scope == "session":
        sid = session_id if session_id is not None else sim.session_id
        if sid is None:
            # No durable session (fresh DB / stats disabled) — an empty board, not an error.
            return LeaderboardOut(
                scope=scope, session_id=None, market_mode=sim.market_mode, rows=[]
            )
        session_row = await db.get(MarketSession, sid)
        if session_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "market session not found")
        price_by_session = {sid: float(session_row.price_ref)}
        finals = await _session_finals(db, [sid])
        market_mode = session_row.market_mode
        out_session_id: int | None = sid
    else:
        sessions = (
            (
                await db.execute(
                    select(MarketSession).where(MarketSession.market_mode == sim.market_mode)
                )
            )
            .scalars()
            .all()
        )
        price_by_session = {s.id: float(s.price_ref) for s in sessions}
        finals = await _session_finals(db, [s.id for s in sessions])
        market_mode = sim.market_mode
        out_session_id = None

    # Fold per-(session, identity) finals into one row per identity. PnL, trades and
    # energy are per-boot cumulative, so summing session finals gives lifetime totals;
    # the score denominator sums each session's own endowment x hours revenue scale.
    by_identity: dict[str, dict] = {}
    for snap, hours in finals:
        key = _identity(snap.name, snap.managed_def_id)
        price_ref = price_by_session.get(snap.session_id, 50.0)
        denominator = revenue_scale_usd(_endowment(snap), price_ref, hours)
        entry = by_identity.setdefault(
            key,
            {
                "latest": snap,
                "pnl": 0.0,
                "denominator": 0.0,
                "hours": 0.0,
                "trades": 0,
                "bought": 0.0,
                "sold": 0.0,
                "sessions": 0,
            },
        )
        if snap.id > entry["latest"].id:
            entry["latest"] = snap
        entry["pnl"] += float(snap.pnl_usd)
        entry["denominator"] += denominator
        entry["hours"] += hours
        entry["trades"] += snap.trade_count
        entry["bought"] += snap.energy_bought_kwh
        entry["sold"] += snap.energy_sold_kwh
        entry["sessions"] += 1

    live_arb = await _live_arb_metrics(sim)
    rows: list[LeaderboardRow] = []
    for key, entry in by_identity.items():
        latest: VppStatSnapshot = entry["latest"]
        if category is not None and latest.category != category:
            continue
        score = entry["pnl"] / entry["denominator"] if entry["denominator"] > 0 else 0.0
        arb = live_arb.get(key, {})
        rows.append(
            LeaderboardRow(
                identity=key,
                name=latest.name,
                managed_def_id=latest.managed_def_id,
                category=latest.category,
                strategy=latest.strategy,
                is_llm=latest.is_llm,
                llm_model=latest.llm_model,
                pnl_usd=f"{entry['pnl']:.4f}",
                score=score,
                spread_capture=arb.get("spread_capture"),
                realized_arb_profit=arb.get("realized_arb_profit"),
                oracle_arb_profit=arb.get("oracle_arb_profit"),
                trade_count=entry["trades"],
                energy_bought_kwh=entry["bought"],
                energy_sold_kwh=entry["sold"],
                soc_frac=latest.soc_frac,
                sessions_count=entry["sessions"],
                hours=entry["hours"],
                last_seen_at=_as_utc(latest.wall_ts),
            )
        )
    rows.sort(key=lambda r: r.score, reverse=True)
    return LeaderboardOut(
        scope=scope, session_id=out_session_id, market_mode=market_mode, rows=rows
    )


@router.get("/history", response_model=HistoryOut)
async def history(
    db: DbSession,
    sim: SimulatorDep,
    name: str | None = None,
    managed_def_id: int | None = None,
    session_id: int | None = None,
    max_points: int = Query(default=500, ge=2, le=5000),
) -> HistoryOut:
    """One identity's snapshot series within a session (default: the running session) —
    the server-side equity curve that survives page refreshes and backend restarts."""
    if (name is None) == (managed_def_id is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "pass exactly one of name= or managed_def_id=",
        )
    sid = session_id if session_id is not None else sim.session_id
    if sid is None:
        latest = (
            await db.execute(select(func.max(MarketSession.id)))
        ).scalar_one_or_none()
        sid = latest
    if sid is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no market sessions recorded yet")

    stmt = select(VppStatSnapshot).where(VppStatSnapshot.session_id == sid)
    if managed_def_id is not None:
        stmt = stmt.where(VppStatSnapshot.managed_def_id == managed_def_id)
    else:
        stmt = stmt.where(VppStatSnapshot.name == name)
    snaps = list((await db.execute(stmt.order_by(VppStatSnapshot.id))).scalars().all())
    if not snaps:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no snapshots for this identity/session")

    # Stride-downsample to max_points, always keeping the newest sample.
    if len(snaps) > max_points:
        stride = -(-len(snaps) // max_points)  # ceil
        sampled = snaps[::stride]
        if sampled[-1].id != snaps[-1].id:
            sampled.append(snaps[-1])
        snaps = sampled

    return HistoryOut(
        identity=_identity(snaps[0].name, snaps[0].managed_def_id),
        session_id=sid,
        points=[
            EquityPoint(
                tick_no=s.tick_no,
                sim_ts=_as_utc(s.sim_ts),
                wall_ts=_as_utc(s.wall_ts),
                pnl_usd=str(s.pnl_usd),
                soc_frac=s.soc_frac,
            )
            for s in snaps
        ],
    )
