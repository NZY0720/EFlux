"""WebSocket stream of market events.

Auth is optional for read-only market data (campus deployment, not public). Pass
?token=<session_or_api_key> if you want it authenticated for logging.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from eflux.bridge import get_bus

router = APIRouter(tags=["ws"])
log = logging.getLogger(__name__)


@router.websocket("/ws/market")
async def market_stream(ws: WebSocket) -> None:
    await ws.accept()
    bus = get_bus()
    try:
        async for event in bus.subscribe():
            payload = event.model_dump(mode="json")
            await ws.send_text(json.dumps(payload, default=str))
    except WebSocketDisconnect:
        return
    except Exception:
        log.exception("WS market stream error")
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
