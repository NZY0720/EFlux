from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

import eflux.db.models  # noqa: F401
from eflux.api.main import _apply_pending_migrations_if_managed
from eflux.config import PROJECT_ROOT
from eflux.db.base import Base


@pytest.mark.asyncio
async def test_fresh_db_skips_alembic_upgrade(tmp_path, monkeypatch):
    def _boom(cfg, rev):
        raise AssertionError("upgrade must not run on a fresh (unmanaged) DB")

    monkeypatch.setattr("alembic.command.upgrade", _boom)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fresh.db")
    try:
        managed = await _apply_pending_migrations_if_managed(engine)
    finally:
        await engine.dispose()
    assert managed is False


@pytest.mark.asyncio
async def test_managed_db_triggers_alembic_upgrade(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("alembic.command.upgrade", lambda cfg, rev: calls.append(rev))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/managed.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
        managed = await _apply_pending_migrations_if_managed(engine)
    finally:
        await engine.dispose()
    assert calls == ["head"]
    assert managed is True


@pytest.mark.asyncio
async def test_managed_db_does_not_hide_upgrade_failure(tmp_path, monkeypatch):
    def _fail(cfg, rev):
        raise RuntimeError("broken migration")

    monkeypatch.setattr("alembic.command.upgrade", _fail)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/drifted.db")
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
        with pytest.raises(RuntimeError, match="broken migration"):
            await _apply_pending_migrations_if_managed(engine)
    finally:
        await engine.dispose()


def test_alembic_reconciles_tables_created_ahead_of_revision(tmp_path):
    db_path = tmp_path / "create-all-drift.db"
    env = {
        **os.environ,
        "EFLUX_DB_URL": f"sqlite+aiosqlite:///{db_path}",
        "PYTHONPATH": str(PROJECT_ROOT / "src"),
    }

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0008"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(db_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        vpp_columns = {row[1] for row in connection.execute("PRAGMA table_info(vpps)").fetchall()}
        snapshot_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(vpp_stat_snapshots)").fetchall()
        }
    assert revision == ("0010",)
    assert {"release_id", "release_content_sha256"} <= vpp_columns
    assert {
        "release_id",
        "release_content_sha256",
        "imbalance_kwh",
        "degradation_cost_usd",
        "llm_cost_usd",
        "gateway_rejections",
        "fallback_count",
    } <= snapshot_columns
