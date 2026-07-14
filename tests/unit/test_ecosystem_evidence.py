from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from eflux.db.models import AgentRelease, MarketSession, User, VppStatSnapshot
from eflux.ecosystem.evaluation import (
    _hidden_population_packs,
    _live_evidence,
    _population_evidence,
)
from eflux.ecosystem.runtime import bench_roster_from_population


def test_worker_hidden_rosters_are_runnable_but_evidence_discloses_only_fingerprint() -> None:
    packs = _hidden_population_packs()

    assert len(packs) >= 2
    for pack in packs:
        roster = bench_roster_from_population(pack, seed=123)
        public = _population_evidence(pack)
        assert roster
        assert public["worker_hidden"] is True
        assert public["spec"]["roster_fingerprint"] == pack["content_sha256"]
        assert "roster" not in public["spec"]


async def test_verified_live_evidence_excludes_polluted_snapshots(db_session) -> None:
    owner = User(email="verified-live-owner@example.com")
    other = User(email="verified-live-other@example.com")
    db_session.add_all([owner, other])
    await db_session.flush()
    release = AgentRelease(
        owner_id=owner.id,
        name="live-release",
        version="1",
        market="realprice",
        visibility="private",
        status="published",
        recipe={"algorithm": "scripted"},
        state={},
        compatibility={},
        environment={},
        badges=[],
        content_sha256="a" * 64,
    )
    db_session.add(release)
    now = datetime(2026, 7, 14, tzinfo=UTC)
    old_session = MarketSession(
        market_mode="realprice", started_at=now - timedelta(days=1), price_ref=Decimal("50")
    )
    live_session = MarketSession(
        market_mode="realprice", started_at=now, price_ref=Decimal("50")
    )
    db_session.add_all([old_session, live_session])
    await db_session.flush()

    def snapshot(
        *,
        session_id: int,
        owner_id: int,
        managed_def_id: int,
        mode: str,
        tick: int,
        pnl: str,
        wall_offset: int,
    ) -> VppStatSnapshot:
        return VppStatSnapshot(
            session_id=session_id,
            vpp_id=-managed_def_id,
            name="live-release",
            managed_def_id=managed_def_id,
            owner_id=owner_id,
            deployment_mode=mode,
            strategy="scripted",
            tick_no=tick,
            sim_ts=now + timedelta(minutes=tick),
            wall_ts=now + timedelta(seconds=wall_offset),
            pnl_usd=Decimal(pnl),
            release_id=release.id,
            release_content_sha256=release.content_sha256,
        )

    target = [
        snapshot(
            session_id=live_session.id,
            owner_id=owner.id,
            managed_def_id=17,
            mode="live",
            tick=1,
            pnl="10",
            wall_offset=10,
        ),
        snapshot(
            session_id=live_session.id,
            owner_id=owner.id,
            managed_def_id=17,
            mode="live",
            tick=2,
            pnl="15",
            wall_offset=20,
        ),
    ]
    polluted = [
        snapshot(
            session_id=live_session.id,
            owner_id=other.id,
            managed_def_id=17,
            mode="live",
            tick=3,
            pnl="9999",
            wall_offset=30,
        ),
        snapshot(
            session_id=live_session.id,
            owner_id=owner.id,
            managed_def_id=17,
            mode="shadow",
            tick=4,
            pnl="8888",
            wall_offset=40,
        ),
        snapshot(
            session_id=live_session.id,
            owner_id=owner.id,
            managed_def_id=99,
            mode="live",
            tick=5,
            pnl="7777",
            wall_offset=50,
        ),
        snapshot(
            session_id=old_session.id,
            owner_id=owner.id,
            managed_def_id=17,
            mode="live",
            tick=6,
            pnl="6666",
            wall_offset=-100,
        ),
    ]
    db_session.add_all([*target, *polluted])
    await db_session.flush()

    metrics, evidence = await _live_evidence(
        db_session, release, {"managed_def_id": 17}
    )

    assert metrics["snapshot_count"] == 2
    assert metrics["net_pnl_usd"] == 5.0
    assert evidence["snapshot_ids"] == [row.id for row in target]
    assert evidence["market_session_id"] == live_session.id
    assert evidence["owner_id"] == owner.id
    assert evidence["deployment_mode"] == "live"
