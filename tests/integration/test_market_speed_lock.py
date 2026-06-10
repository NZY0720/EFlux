"""External orders are only allowed at market_speed=1.0 (realtime).

Setting EFLUX_MARKET_SPEED to a fast mode should make POST /orders return 409.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_external_order_rejected_at_fast_speed(monkeypatch, db_session):
    monkeypatch.setenv("EFLUX_MARKET_SPEED", "10.0")

    # Reset settings cache so the app picks up the new speed.
    from eflux.config import get_settings
    from eflux.db.session import get_engine, get_sessionmaker
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    from eflux.api.main import create_app
    from httpx import ASGITransport, AsyncClient

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/auth/magic-link", json={"email": "fast@hku.hk"})
            tok = r.json()["dev_token"]
            r = await client.post("/auth/consume", json={"token": tok})
            headers = {"Authorization": f"Bearer {r.json()['session_token']}"}

            r = await client.post("/vpps", headers=headers, json={"name": "speed-vpp", "params": {}})
            vpp_id = r.json()["id"]

            r = await client.post(
                "/orders",
                headers=headers,
                json={"vpp_id": vpp_id, "side": "buy", "price": "80", "qty": "0.05"},
            )
            assert r.status_code == 409, r.text
            assert "realtime" in r.text.lower()
