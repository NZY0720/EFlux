"""FastAPI dependencies — auth, db session, simulator handle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from eflux.auth.api_key import verify_api_key
from eflux.auth.session import get_user_for_session_token
from eflux.db.models import User
from eflux.db.session import get_db
from eflux.forecasting.service import ForecastService
from eflux.simulator.runner import Simulator


async def db_session() -> AsyncIterator[AsyncSession]:
    async for s in get_db():
        yield s


DbSession = Annotated[AsyncSession, Depends(db_session)]


async def current_user(
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve user from a Bearer token (session token OR API key)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    # Try API key first (cheaper if it matches prefix), then session.
    user = await verify_api_key(session, token)
    if user is None:
        user = await get_user_for_session_token(session, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def get_simulator(request: Request) -> Simulator:
    sim: Simulator | None = getattr(request.app.state, "simulator", None)
    if sim is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="simulator not running",
        )
    return sim


SimulatorDep = Annotated[Simulator, Depends(get_simulator)]


def get_forecast_service(request: Request) -> ForecastService:
    # Resolve through the simulator first: the forecast bootstrap task may replace
    # sim.forecast_service (e.g. checkpoint restore) after app.state captured the
    # original object, which would otherwise pin the API to a stale empty service.
    sim: Simulator | None = getattr(request.app.state, "simulator", None)
    service: ForecastService | None = getattr(sim, "forecast_service", None) if sim is not None else None
    if service is None:
        service = getattr(request.app.state, "forecast_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="forecast service not running",
        )
    return service


ForecastServiceDep = Annotated[ForecastService, Depends(get_forecast_service)]
