"""FastAPI app factory + lifespan + router mounting."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure all models are imported before create_all.
import eflux.db.models  # noqa: F401
from eflux import __version__
from eflux.api.routers import auth, health, market, orders, vpps
from eflux.api.ws import market as market_ws
from eflux.bridge import InMemoryBus, set_bus
from eflux.bridge.bus import EventBus
from eflux.config import get_settings
from eflux.db.base import Base
from eflux.db.session import get_engine
from eflux.simulator.runner import Simulator
from eflux.simulator.scenarios import load_default_scenario, provision_managed_vpp

log = logging.getLogger(__name__)


async def _build_bus(settings) -> EventBus:
    if settings.bus_backend == "redis":
        from eflux.bridge.redis_bus import RedisStreamBus

        bus = RedisStreamBus(settings.redis_url)
        try:
            await bus.ping()
            log.info("Using RedisStreamBus at %s", settings.redis_url)
            return bus
        except Exception as e:
            log.warning("Redis at %s unreachable (%s) — falling back to InMemoryBus", settings.redis_url, e)
            await bus.close()
    log.info("Using InMemoryBus")
    return InMemoryBus()


async def _rehydrate_managed_vpps(sim: Simulator) -> None:
    """Re-provision persisted managed agents (Tier 0) so they survive a restart. Only the agent
    *definitions* persist (vpps.is_managed rows); the live market state is ephemeral."""
    from sqlalchemy import select

    from eflux.db.models import VPP
    from eflux.db.session import get_sessionmaker

    count = 0
    async with get_sessionmaker()() as session:
        rows = (
            (
                await session.execute(
                    select(VPP).where(VPP.is_managed.is_(True), VPP.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            cfg = row.managed_config or {}
            try:
                provision_managed_vpp(
                    sim,
                    owner_id=row.owner_id,
                    name=row.name,
                    params=row.params,
                    persona_prompt=cfg.get("persona"),
                    agent_params=cfg.get("agent_params") or {},
                    seed=cfg.get("seed"),
                    managed_def_id=row.id,
                )
                count += 1
            except Exception:
                log.exception("Failed to rehydrate managed VPP id=%s name=%s", row.id, row.name)
    if count:
        log.info("Rehydrated %d managed VPP(s) from the DB", count)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    log.info("EFlux starting (env=%s, market_speed=%sx)", settings.env, settings.market_speed)

    # 1. Init DB schema. Dev convenience path runs create_all so a fresh SQLite
    #    file works without `alembic upgrade head`. Production should set
    #    EFLUX_AUTO_CREATE_SCHEMA=false and rely on alembic exclusively.
    if settings.env == "dev" and settings.auto_create_schema:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("DB schema ensured (dev create_all)")
    else:
        log.info("Skipping create_all (auto_create_schema=%s, env=%s) — run alembic upgrade head", settings.auto_create_schema, settings.env)

    # 2. Event bus. Selects InMemoryBus or RedisStreamBus per settings; if Redis
    #    is configured but unreachable, log a warning and fall back to memory.
    bus = await _build_bus(settings)
    set_bus(bus)
    app.state.bus = bus

    # 3. Simulator.
    sim = Simulator(bus=bus)
    load_default_scenario(sim)
    sim.refresh_data_sources()
    log.info("Data sources checked: %s", sim.data_source_status().get("summary"))
    await sim.start()
    app.state.simulator = sim
    log.info("Simulator started with %d built-in VPPs", len(sim.vpps))

    try:
        yield
    finally:
        log.info("EFlux shutting down")
        await sim.stop()
        await bus.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="EFlux — VPP Electricity Trading Platform",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(vpps.router)
    app.include_router(orders.router)
    app.include_router(market.router)
    app.include_router(market_ws.router)

    return app


app = create_app()
