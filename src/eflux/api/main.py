"""FastAPI app factory + lifespan + router mounting."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

# Ensure all models are imported before create_all.
import eflux.db.models  # noqa: F401
from eflux import __version__
from eflux.agents.reflective.pool import CURATED_MODELS
from eflux.agents.reflective.strategist import external_guidance_from_dict
from eflux.api.routers import (
    auth,
    benchmarks,
    competitions,
    forecasts,
    health,
    leaderboard,
    market,
    orders,
    proveout,
    vpps,
)
from eflux.api.ws import market as market_ws
from eflux.bridge import InMemoryBus, set_bus
from eflux.bridge.bus import EventBus
from eflux.config import get_settings
from eflux.db.base import Base
from eflux.db.models import AuditEvent, Competition, CompetitionRuleSet
from eflux.db.session import get_engine, get_sessionmaker
from eflux.simulator.runner import Simulator
from eflux.simulator.scenarios import (
    apply_chat_prefs,
    apply_external_guidance,
    load_default_scenario,
    normalize_managed_config,
    provision_managed_vpp,
)
from eflux.stats.session import close_market_session, open_market_session, prune_old_snapshots

log = logging.getLogger(__name__)

_SEASON_0_RULES = {
    "window_sec": 300,
    "deadline_ms": 500,
    "practice_seeds": 3,
    "hidden_seeds": 5,
    "holdout_seeds": 2,
    "submissions_per_day": 2,
    "seed_hours": 24,
}


async def _apply_pending_migrations_if_managed(engine) -> None:
    """Upgrade an alembic-managed dev DB to head before create_all runs.

    Forgiving on purpose: a failed upgrade logs loudly and the boot continues on
    create_all, so a drifted dev database never bricks startup — but new columns
    on pre-existing tables stay missing until the DB is reconciled manually.
    """
    from sqlalchemy import inspect as sa_inspect

    async with engine.connect() as conn:
        managed = await conn.run_sync(lambda sync_conn: sa_inspect(sync_conn).has_table("alembic_version"))
    if not managed:
        log.info("DB is not alembic-managed; create_all only (run `alembic stamp head` to adopt migrations)")
        return

    def _upgrade() -> None:
        from alembic.config import Config as AlembicConfig

        from alembic import command
        from eflux.config import PROJECT_ROOT

        cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
        # Never let env.py's fileConfig reset app logging mid-boot (it muted
        # every post-migration log line on 2026-07-11).
        cfg.attributes["skip_logging_config"] = True
        command.upgrade(cfg, "head")

    try:
        await asyncio.to_thread(_upgrade)
        log.info("Alembic migrations applied (head)")
    except Exception:
        log.exception(
            "Alembic auto-upgrade failed — continuing on create_all; new columns on "
            "existing tables may be missing until the DB is reconciled"
        )


async def _seed_default_competition() -> None:
    """Create the first public competition once, including its auditable seed event."""
    async with get_sessionmaker()() as session:
        if (await session.execute(select(Competition.id).limit(1))).scalar_one_or_none() is not None:
            return
        competition = Competition(
            slug="season-0",
            title="EFlux Open — Season 0",
            description="",
            status="open",
        )
        session.add(competition)
        await session.flush()
        session.add(
            CompetitionRuleSet(
                competition_id=competition.id,
                version="rules-v1.1",
                track="managed",
                config=_SEASON_0_RULES,
            )
        )
        session.add(
            AuditEvent(
                actor_user_id=None,
                action="competition.seeded",
                entity_type="competition",
                entity_id=competition.id,
                payload={"slug": competition.slug},
            )
        )
        await session.commit()


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


def _rehydrate_model(model: str | None) -> str | None:
    if model is None or model in CURATED_MODELS:
        return model
    log.warning(
        "Persisted managed-agent model %r is no longer curated; falling back to default model",
        model,
    )
    return None


def _validated_external_guidance(cfg: dict, *, market_mode: str) -> tuple[dict | None, dict, bool]:
    guidance = cfg.get("external_guidance")
    if cfg.get("guidance_mode") != "external":
        return None, cfg, False
    if not isinstance(guidance, dict):
        scrubbed = {**cfg, "guidance_mode": "platform"}
        scrubbed.pop("external_guidance", None)
        return None, scrubbed, True
    try:
        for key in ("risk_budget", "price_bias_bps", "soc_target"):
            if key in guidance:
                float(guidance[key])
        external_guidance_from_dict(guidance, market_mode=market_mode)
    except Exception:
        log.exception("Persisted external guidance is invalid; scrubbing it from managed_config")
        scrubbed = {**cfg, "guidance_mode": "platform"}
        scrubbed.pop("external_guidance", None)
        return None, scrubbed, True
    return guidance, cfg, False


async def _rehydrate_managed_vpps(sim: Simulator) -> None:
    """Re-provision persisted managed agents (Tier 0) so they survive a restart. Only the agent
    *definitions* persist (vpps.is_managed rows); the live market state is ephemeral."""
    from sqlalchemy import select

    from eflux.db.models import VPP
    from eflux.db.session import get_sessionmaker

    count = 0
    # Never let an optional-feature startup hook crash the server: a DB outage or a
    # not-yet-migrated schema (missing is_managed/managed_config) must degrade gracefully.
    try:
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
            scrubbed_any = False
            for row in rows:
                cfg = dict(row.managed_config or {})
                # Translate legacy fused values (hybrid / removed zi) to (base, llm_enabled).
                algorithm, llm_enabled = normalize_managed_config(cfg)
                online_learning = cfg.get("online_learning", True)
                if llm_enabled:
                    guidance, cfg, scrubbed = _validated_external_guidance(
                        cfg, market_mode=sim.market_mode
                    )
                else:
                    guidance, scrubbed = None, False
                if scrubbed:
                    row.managed_config = cfg
                    scrubbed_any = True
                try:
                    vpp = provision_managed_vpp(
                        sim,
                        owner_id=row.owner_id,
                        name=row.name,
                        params=row.params,
                        persona_prompt=cfg.get("persona"),
                        agent_params=cfg.get("agent_params") or {},
                        seed=cfg.get("seed"),
                        model=_rehydrate_model(cfg.get("model")),
                        managed_def_id=row.id,
                        algorithm=algorithm,
                        llm_enabled=llm_enabled,
                        online_learning=online_learning,
                    )
                    # Restore external steering (Tier A3) so a restart neither burns
                    # platform LLM calls nor forgets the owner's last guidance.
                    if llm_enabled and guidance is not None:
                        apply_external_guidance(vpp, guidance, market_mode=sim.market_mode)
                    apply_chat_prefs(vpp, cfg.get("chat"))
                    count += 1
                except Exception:
                    log.exception(
                        "Failed to rehydrate managed VPP id=%s name=%s", row.id, row.name
                    )
            if scrubbed_any:
                await session.commit()
    except Exception:
        log.exception("Managed-VPP rehydration skipped (DB unavailable or schema not migrated?)")
    if count:
        log.info("Rehydrated %d managed VPP(s) from the DB", count)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    log.info("EFlux starting (env=%s, market_speed=%sx)", settings.env, settings.market_speed)

    # 1. Init DB schema. For alembic-managed DBs, apply pending migrations first —
    #    create_all alone adds new TABLES but silently skips new COLUMNS on
    #    existing ones (2026-07-10: users.role absent at stamp 0002 → 500 on
    #    sign-in). Fresh DBs (no alembic_version — e.g. test databases) keep the
    #    plain create_all path. Production sets EFLUX_AUTO_CREATE_SCHEMA=false
    #    and relies on alembic exclusively.
    if settings.env == "dev" and settings.auto_create_schema:
        engine = get_engine()
        await _apply_pending_migrations_if_managed(engine)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("DB schema ensured (dev create_all)")
    else:
        log.info("Skipping create_all (auto_create_schema=%s, env=%s) — run alembic upgrade head", settings.auto_create_schema, settings.env)

    await _seed_default_competition()

    # 2. Event bus. Selects InMemoryBus or RedisStreamBus per settings; if Redis
    #    is configured but unreachable, log a warning and fall back to memory.
    bus = await _build_bus(settings)
    set_bus(bus)
    app.state.bus = bus

    # 3. Simulator.
    sim = Simulator(bus=bus)
    load_default_scenario(sim)
    # Re-provision user-owned managed agents (Tier 0) persisted in the DB, so they
    # rejoin the market after a restart alongside the roster.
    await _rehydrate_managed_vpps(sim)
    sim.refresh_data_sources()
    log.info("Data sources checked: %s", sim.data_source_status().get("summary"))
    # 4. Durable results: open this boot's market-session row (leaderboard identity)
    #    and prune snapshots past retention. Best-effort — session_id stays None (and
    #    the snapshot writer stays off) if the DB/schema isn't ready.
    await prune_old_snapshots()
    sim.session_id = await open_market_session(sim)
    await sim.start()
    app.state.simulator = sim
    app.state.forecast_service = sim.forecast_service
    log.info("Simulator started with %d built-in VPPs", len(sim.vpps))

    try:
        yield
    finally:
        log.info("EFlux shutting down")
        await sim.stop()
        await close_market_session(sim.session_id)
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
    app.include_router(competitions.router)
    app.include_router(competitions.submissions_router)
    app.include_router(vpps.router)
    app.include_router(orders.router)
    app.include_router(proveout.router)
    app.include_router(market.router)
    app.include_router(forecasts.router)
    app.include_router(leaderboard.router)
    app.include_router(benchmarks.router)
    app.include_router(market_ws.router)

    return app


app = create_app()
