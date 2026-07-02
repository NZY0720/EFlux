"""Integration-test fixtures.

Brings up a fresh FastAPI app (with lifespan → DB schema + simulator) per test using
the httpx ASGI transport.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _reset_order_router_state():
    """Clear the order router's in-memory rate-limit buckets + idempotency cache between
    tests. User ids restart per fresh DB, so this module-level state would otherwise leak
    across tests (a depleted bucket or a stale cached response for a reused user id)."""
    from eflux.api.routers import orders as _orders

    _orders._buckets.clear()
    _orders._idempotency.clear()
    yield


@pytest_asyncio.fixture
async def client(db_session) -> AsyncIterator[AsyncClient]:
    """Yield an httpx AsyncClient bound to a fresh app whose lifespan is running.

    The db_session fixture (from tests/conftest.py) handles schema setup/teardown
    on the same throwaway SQLite file the app will read via Settings.
    """
    # Import inside the fixture so settings cache reset from tests/conftest is honored.
    from eflux.api.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
