"""Health + meta endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from eflux import __version__
from eflux.config import get_settings

router = APIRouter(prefix="", tags=["meta"])


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.get("/")
def root(request: Request) -> dict:
    settings = get_settings()
    sim = getattr(request.app.state, "simulator", None)
    return {
        "name": "EFlux",
        "version": __version__,
        "env": settings.env,
        "market_mode": settings.market_mode,
        "market_speed": settings.market_speed,
        "vpps_builtin": len(sim.vpps) if sim else 0,
        "docs": "/docs",
    }
