"""Integration-test fixtures.

Brings up a fresh FastAPI app (with lifespan → DB schema + simulator) per test using
the httpx ASGI transport.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient


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
