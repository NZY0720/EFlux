"""Alembic environment — async-aware, reads DB URL from eflux.config.Settings."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

# Importing models registers them on Base.metadata so autogenerate sees them.
import eflux.db.models  # noqa: F401
from alembic import context
from eflux.config import get_settings
from eflux.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolved_db_url() -> str:
    """Pull the URL from app settings at runtime (alembic.ini leaves it blank)."""
    return get_settings().db_url


def _is_async_url(url: str) -> bool:
    return "+aiosqlite" in url or "+asyncpg" in url


def _sync_url(url: str) -> str:
    """For offline mode and migrations we want a plain sync driver, not the async one."""
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection."""
    context.configure(
        url=_sync_url(_resolved_db_url()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite needs batch ops for ALTER
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Online mode for async URLs (sqlite+aiosqlite, postgresql+asyncpg)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    connectable = create_async_engine(_resolved_db_url(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    url = _resolved_db_url()
    if _is_async_url(url):
        asyncio.run(run_async_migrations())
        return

    # Plain sync URL — straight engine_from_config path.
    cfg_section = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = url
    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
