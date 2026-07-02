"""Market-session lifecycle: open a durable session row per boot, close it on shutdown,
prune old snapshots. All helpers are best-effort — a DB outage or a not-yet-migrated
schema must degrade to "no durability" (session_id stays None), never crash startup.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from eflux.config import PROJECT_ROOT, get_settings
from eflux.data.caiso_reference import caiso_reference_price
from eflux.db.models import MarketSession, VppStatSnapshot
from eflux.db.session import get_sessionmaker

if TYPE_CHECKING:  # pragma: no cover - typing only
    from eflux.simulator.runner import Simulator

log = logging.getLogger(__name__)


def _scenario_sha256(scenario_file: str) -> str | None:
    path = Path(scenario_file)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


async def open_market_session(sim: Simulator) -> int | None:
    """Insert this boot's market_sessions row and return its id (None on failure)."""
    settings = get_settings()
    try:
        async with get_sessionmaker()() as session:
            row = MarketSession(
                market_mode=sim.market_mode,
                started_at=datetime.now(UTC),
                # Memoized per process (static default or file-cached CAISO mean) — the
                # same anchor the agents' cost bases were calibrated to at scenario load.
                price_ref=Decimal(str(caiso_reference_price(default=50.0))),
                scenario_file=settings.scenario_file,
                scenario_sha256=_scenario_sha256(settings.scenario_file),
                market_speed=settings.market_speed,
                tick_sim_sec=settings.market_tick_sec,
            )
            session.add(row)
            await session.commit()
            log.info("Opened market session id=%d (mode=%s)", row.id, row.market_mode)
            return row.id
    except Exception:
        log.exception("Market-session open failed — results will not persist this run")
        return None


async def close_market_session(session_id: int | None) -> None:
    """Stamp ended_at on clean shutdown. Crashed runs keep ended_at NULL by design."""
    if session_id is None:
        return
    try:
        async with get_sessionmaker()() as session:
            row = await session.get(MarketSession, session_id)
            if row is not None:
                row.ended_at = datetime.now(UTC)
                await session.commit()
    except Exception:
        log.exception("Market-session close failed (id=%s)", session_id)


async def prune_old_snapshots() -> None:
    """Drop snapshot rows (and empty sessions) older than the retention window."""
    settings = get_settings()
    days = settings.stats_retention_days
    if days <= 0:
        return
    cutoff = datetime.now(UTC) - timedelta(days=days)
    try:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                delete(VppStatSnapshot).where(VppStatSnapshot.wall_ts < cutoff)
            )
            # Sessions whose snapshots were all pruned carry no leaderboard signal — drop them.
            stale = await session.execute(
                delete(MarketSession).where(
                    MarketSession.started_at < cutoff,
                    ~MarketSession.id.in_(select(VppStatSnapshot.session_id).distinct()),
                )
            )
            await session.commit()
            pruned = getattr(result, "rowcount", 0) or 0
            dropped = getattr(stale, "rowcount", 0) or 0
            if pruned:
                log.info(
                    "Pruned %d stat snapshot(s) older than %dd (%d empty session(s) dropped)",
                    pruned,
                    days,
                    dropped,
                )
    except Exception:
        log.exception("Stat-snapshot prune failed")
