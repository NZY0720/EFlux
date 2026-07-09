"""Public forecast endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from eflux.api.deps import ForecastServiceDep
from eflux.forecasting.service import HISTORY_MAXLEN

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


@router.get("/latest")
def latest_forecast(service: ForecastServiceDep) -> dict[str, Any]:
    payload = service.latest.to_dict()
    # Pre-warm-up bundles are zero-valued placeholders; consumers (UI, external
    # participant bots) need a machine-readable signal to not trade on them.
    payload["warm"] = service.is_warm
    return payload


@router.get("/history")
def forecast_history(
    service: ForecastServiceDep,
    limit: int = Query(720, ge=1),
    target: str | None = None,
) -> list[dict[str, Any]]:
    try:
        return service.history(limit=min(limit, HISTORY_MAXLEN), target=target)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
