from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from eflux.api.main import _apply_pending_migrations_if_managed


@pytest.mark.asyncio
async def test_fresh_db_skips_alembic_upgrade(tmp_path, monkeypatch):
    def _boom(cfg, rev):
        raise AssertionError("upgrade must not run on a fresh (unmanaged) DB")

    monkeypatch.setattr("alembic.command.upgrade", _boom)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fresh.db")
    try:
        await _apply_pending_migrations_if_managed(engine)
    finally:
        await engine.dispose()


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
        await _apply_pending_migrations_if_managed(engine)
    finally:
        await engine.dispose()
    assert calls == ["head"]
