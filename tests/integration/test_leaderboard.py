"""Integration tests for durable results: market sessions + /leaderboard endpoints.

Seeds market_sessions/vpp_stat_snapshots rows directly (deterministic — no dependence
on the live snapshot cadence) and asserts the endpoint math; separately proves the
restart-survival story: every backend boot opens its own session row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from eflux.db.models import MarketSession, VppStatSnapshot

pytestmark = pytest.mark.asyncio


def _snap(session_id: int, name: str, *, tick_no: int, sim_ts: datetime, pnl: str, **over):
    row = dict(
        session_id=session_id,
        vpp_id=-1,
        name=name,
        managed_def_id=None,
        owner_id=None,
        strategy="AAAgent",
        category="solar",
        is_llm=False,
        llm_model=None,
        tick_no=tick_no,
        sim_ts=sim_ts,
        wall_ts=sim_ts,
        pnl_usd=Decimal(pnl),
        soc_kwh=5.0,
        soc_frac=0.5,
        energy_bought_kwh=1.0,
        energy_sold_kwh=2.0,
        trade_count=tick_no,
        pv_kw_peak=10.0,
        wind_kw_rated=0.0,
        battery_kwh=10.0,
        battery_kw_max=0.0,
        load_kw_base=0.0,
        gas_kw_max=0.0,
    )
    row.update(over)
    return VppStatSnapshot(**row)


async def _seed_session(db_session, *, mode="p2p", price_ref="50.0") -> int:
    row = MarketSession(
        market_mode=mode,
        started_at=datetime.now(UTC),
        price_ref=Decimal(price_ref),
        scenario_file="scenarios/default.yaml",
        market_speed=1.0,
        tick_sim_sec=1.0,
    )
    db_session.add(row)
    await db_session.commit()
    return row.id


async def test_session_leaderboard_ranks_by_normalized_score(client, db_session):
    sid = await _seed_session(db_session)
    t0 = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    t10 = t0 + timedelta(hours=10)
    # small-endowment agent: $5 on 10 kW over 10 h -> score 1.0
    db_session.add(_snap(sid, "small", tick_no=1, sim_ts=t0, pnl="0"))
    db_session.add(_snap(sid, "small", tick_no=100, sim_ts=t10, pnl="5"))
    # big-endowment agent: same $5 on 100 kW -> score 0.1 despite equal PnL
    db_session.add(_snap(sid, "big", tick_no=1, sim_ts=t0, pnl="0", pv_kw_peak=100.0))
    db_session.add(_snap(sid, "big", tick_no=100, sim_ts=t10, pnl="5", pv_kw_peak=100.0))
    await db_session.commit()

    r = await client.get("/leaderboard", params={"scope": "session", "session_id": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "session"
    assert body["session_id"] == sid
    rows = {row["name"]: row for row in body["rows"]}
    assert rows["small"]["score"] == pytest.approx(1.0)
    assert rows["big"]["score"] == pytest.approx(0.1)
    # Ranked by score, not raw PnL — the equal-PnL big endowment sits below.
    assert [row["name"] for row in body["rows"]] == ["small", "big"]
    assert rows["small"]["pnl_usd"].startswith("5.")
    assert "spread_capture" in rows["small"]
    assert "realized_arb_profit" in rows["small"]
    assert "oracle_arb_profit" in rows["small"]
    assert rows["small"]["trade_count"] == 100  # final row's cumulative counter
    assert rows["small"]["hours"] == pytest.approx(10.0)


async def test_alltime_aggregates_sessions_of_same_mode(client, db_session):
    t0 = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    t10 = t0 + timedelta(hours=10)
    sid1 = await _seed_session(db_session)
    sid2 = await _seed_session(db_session)
    other_mode = await _seed_session(db_session, mode="realprice")
    for sid, pnl in ((sid1, "5"), (sid2, "10")):
        db_session.add(_snap(sid, "vpp-a", tick_no=1, sim_ts=t0, pnl="0"))
        db_session.add(_snap(sid, "vpp-a", tick_no=50, sim_ts=t10, pnl=pnl))
    # A realprice session must not leak into the p2p all-time board.
    db_session.add(_snap(other_mode, "vpp-a", tick_no=1, sim_ts=t0, pnl="999"))
    await db_session.commit()

    r = await client.get("/leaderboard", params={"scope": "alltime"})
    assert r.status_code == 200
    rows = [row for row in r.json()["rows"] if row["name"] == "vpp-a"]
    assert len(rows) == 1
    row = rows[0]
    # sum(pnl) / sum(scale): (5+10) / (2 * 10kW*10h*50/1000) = 15 / 10 = 1.5
    assert row["score"] == pytest.approx(1.5)
    assert row["sessions_count"] == 2
    assert row["hours"] == pytest.approx(20.0)
    assert row["pnl_usd"].startswith("15.")


async def test_history_downsamples_and_survives_identity_lookup(client, db_session):
    sid = await _seed_session(db_session)
    t0 = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    for i in range(40):
        db_session.add(
            _snap(sid, "curve", tick_no=i, sim_ts=t0 + timedelta(minutes=i), pnl=str(i))
        )
    await db_session.commit()

    r = await client.get(
        "/leaderboard/history",
        params={"name": "curve", "session_id": sid, "max_points": 10},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["identity"] == "name:curve"
    points = body["points"]
    assert 2 <= len(points) <= 11
    # Always keeps the newest sample, and points are monotonically ordered.
    assert points[-1]["tick_no"] == 39
    ticks = [p["tick_no"] for p in points]
    assert ticks == sorted(ticks)

    # Exactly one of name / managed_def_id must be passed.
    r = await client.get("/leaderboard/history", params={"session_id": sid})
    assert r.status_code == 422
    r = await client.get(
        "/leaderboard/history",
        params={"name": "curve", "managed_def_id": 3, "session_id": sid},
    )
    assert r.status_code == 422


async def test_category_filter_and_no_email_exposure(client, db_session):
    sid = await _seed_session(db_session)
    t0 = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    db_session.add(_snap(sid, "sun", tick_no=1, sim_ts=t0, pnl="1", category="solar"))
    db_session.add(
        _snap(sid, "peaker", tick_no=1, sim_ts=t0, pnl="2", category="gas", owner_id=42)
    )
    await db_session.commit()

    r = await client.get(
        "/leaderboard", params={"scope": "session", "session_id": sid, "category": "gas"}
    )
    rows = r.json()["rows"]
    assert [row["name"] for row in rows] == ["peaker"]
    # The public board never exposes account fields.
    assert "owner_id" not in rows[0] and "email" not in rows[0]


async def test_each_boot_opens_its_own_session(db_session):
    """Restart-survival: two app lifespans against one DB -> two market_sessions rows,
    and the second boot's current session differs from the first's."""
    from eflux.api.main import create_app

    seen: list[int] = []
    for _ in range(2):
        app = create_app()
        async with app.router.lifespan_context(app):
            sim = app.state.simulator
            assert sim.session_id is not None
            seen.append(sim.session_id)

    assert len(set(seen)) == 2
    rows = (await db_session.execute(select(MarketSession))).scalars().all()
    assert {r.id for r in rows} >= set(seen)
    # Clean shutdowns stamped ended_at on both.
    by_id = {r.id: r for r in rows}
    assert all(by_id[sid].ended_at is not None for sid in seen)


async def test_lifespan_writes_snapshots_on_shutdown(db_session):
    """The stop() flush persists a final sample even if no cadence window elapsed."""
    from eflux.api.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        sid = app.state.simulator.session_id
        assert sid is not None
    rows = (
        (
            await db_session.execute(
                select(VppStatSnapshot).where(VppStatSnapshot.session_id == sid)
            )
        )
        .scalars()
        .all()
    )
    assert rows, "shutdown flush should persist at least one snapshot batch"
    names = {r.name for r in rows}
    assert len(names) >= 30  # the default roster
