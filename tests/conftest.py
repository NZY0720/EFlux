"""Shared pytest fixtures for the EFlux test suite.

Defaults the whole suite to a throwaway SQLite file in /tmp, isolated from `eflux_dev.db`
in the repo root. Tests that need different settings override env vars via monkeypatch
and call `_reset_settings_cache()`.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

# Set env vars BEFORE importing eflux modules so the first `get_settings()` call
# inside any imported module picks them up.
# Process-local so concurrent pytest/Codex sessions cannot unlink another run's open SQLite DB.
_TMP_DB = Path(tempfile.gettempdir()) / f"eflux_pytest_{os.getpid()}.db"
os.environ.setdefault("EFLUX_ENV", "dev")
os.environ.setdefault("EFLUX_DB_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
os.environ.setdefault("EFLUX_BUS_BACKEND", "memory")
os.environ.setdefault("EFLUX_AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("EFLUX_LLM_ENABLED", "false")
# Keep the cost-basis reference static (50) in tests so scenario loading is deterministic and
# never touches the network (config.env sets "caiso" for the live app). os.environ overrides
# the env_file, so this wins over config.env even when set there.
os.environ.setdefault("EFLUX_PRICE_REF_SOURCE", "static")

from eflux.config import get_settings  # noqa: E402
from eflux.db.base import Base  # noqa: E402
from eflux.db.session import get_engine, get_sessionmaker  # noqa: E402


def _reset_settings_cache() -> None:
    """Drop cached Settings + Engine + SessionMaker so env-var changes take effect."""
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest.fixture(autouse=True)
def _settings_cache_isolation() -> None:
    """Fresh settings cache for every test — env-var overrides take effect immediately."""
    _reset_settings_cache()
    yield
    _reset_settings_cache()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator:
    """Yield an AsyncSession against a fresh schema. Drops tables at teardown."""
    # Wipe the file each test for a clean slate.
    if _TMP_DB.exists():
        _TMP_DB.unlink()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    _reset_settings_cache()
