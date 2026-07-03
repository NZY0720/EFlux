"""Agent Protocol v1 — batch orders, state read, and per-account governance (Tier A1).

See docs/AGENT_SPEC.md §"Agent Protocol v1".
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


async def _login(client, email: str = "proto@hku.hk") -> dict[str, str]:
    r = await client.post("/auth/magic-link", json={"email": email})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


async def _make_vpp(client, auth: dict[str, str], name: str) -> int:
    r = await client.post(
        "/vpps", headers=auth, json={"name": name, "params": {"pv_kw_peak": 4.0, "battery_kwh": 10.0}}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_batch_submit_open_cancel(client):
    auth = await _login(client, "batch@hku.hk")
    vpp_id = await _make_vpp(client, auth, "proto-vpp")

    # Two valid sells (priced high so they rest as asks); per-item results echo client_ref.
    r = await client.post(
        "/orders/batch",
        headers=auth,
        json={
            "orders": [
                {"vpp_id": vpp_id, "side": "sell", "price": 900, "qty": 1, "client_ref": "a"},
                {"vpp_id": vpp_id, "side": "sell", "price": 910, "qty": 1, "client_ref": "b"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["protocol_version"] == 1 and isinstance(body["tick_id"], int)
    results = {x["client_ref"]: x for x in body["results"]}
    assert results["a"]["status"] == "accepted" and results["a"]["order_id"] is not None
    assert results["b"]["status"] == "accepted" and results["b"]["order_id"] is not None
    ids = [results["a"]["order_id"], results["b"]["order_id"]]

    # State read shows both resting orders.
    r = await client.get("/orders/open", headers=auth, params={"vpp_id": vpp_id})
    assert r.status_code == 200, r.text
    assert set(ids) <= {o["order_id"] for o in r.json()}

    # An over-cap price is rejected at request validation (422) — it never reaches the book.
    r = await client.post(
        "/orders/batch",
        headers=auth,
        json={"orders": [{"vpp_id": vpp_id, "side": "sell", "price": 2000, "qty": 1}]},
    )
    assert r.status_code == 422

    # Batch cancel removes both.
    r = await client.post("/orders/batch", headers=auth, json={"cancels": ids})
    assert r.status_code == 200, r.text
    assert all(c["ok"] for c in r.json()["cancelled"] if c["order_id"] in ids)
    r = await client.get("/orders/open", headers=auth, params={"vpp_id": vpp_id})
    assert r.json() == []


@pytest.mark.asyncio
async def test_batch_idempotency_replay(client):
    auth = await _login(client, "idem@hku.hk")
    vpp_id = await _make_vpp(client, auth, "idem-vpp")
    payload = {
        "idempotency_key": "abc-123",
        "orders": [{"vpp_id": vpp_id, "side": "sell", "price": 900, "qty": 0.5, "client_ref": "x"}],
    }
    r1 = await client.post("/orders/batch", headers=auth, json=payload)
    assert r1.status_code == 200, r1.text
    r2 = await client.post("/orders/batch", headers=auth, json=payload)
    assert r2.status_code == 200
    # Same key → identical result, and no second order was created.
    assert r1.json()["results"][0]["order_id"] == r2.json()["results"][0]["order_id"]
    opens = (await client.get("/orders/open", headers=auth, params={"vpp_id": vpp_id})).json()
    assert len(opens) == 1


@pytest.mark.asyncio
async def test_batch_idempotency_concurrent_duplicate_guard():
    """Direct router-level concurrency test: ASGI scheduling is unnecessary here."""
    from eflux.api.ratelimit import reset_all_limiters
    from eflux.api.routers import orders as orders_router

    reset_all_limiters()
    orders_router._idempotency.clear()
    orders_router._idempotency_inflight.clear()

    vpp_id = 123
    entered_db = asyncio.Event()
    release_db = asyncio.Event()
    executions = 0

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [vpp_id]

    class Session:
        async def execute(self, stmt):
            del stmt
            entered_db.set()
            await release_db.wait()
            return Result()

    class Book:
        def get(self, order_id):
            del order_id
            return None

    class Engine:
        book = Book()

    class Sim:
        engine = Engine()

        async def submit_external_batch(self, *, orders, cancels):
            nonlocal executions
            executions += 1
            await asyncio.sleep(0)
            return {
                "tick_id": 7,
                "results": [
                    {
                        "index": 0,
                        "client_ref": orders[0].get("client_ref"),
                        "status": "accepted",
                        "order_id": 9001,
                        "remaining_qty": "1",
                        "expires_at_sim": None,
                        "trades": [],
                    }
                ],
                "cancelled": [],
            }

    payload = orders_router.OrderBatch(
        idempotency_key="same-key",
        orders=[{"vpp_id": vpp_id, "side": "sell", "price": "900", "qty": "1", "client_ref": "x"}],
    )
    kwargs = {
        "payload": payload,
        "session": Session(),
        "user": SimpleNamespace(id=1),
        "sim": Sim(),
    }
    first = asyncio.create_task(orders_router.submit_batch(**kwargs))
    await entered_db.wait()
    second = asyncio.create_task(orders_router.submit_batch(**kwargs))
    await asyncio.sleep(0)
    release_db.set()

    r1, r2 = await asyncio.gather(first, second)
    assert executions == 1
    assert r1.results[0].order_id == r2.results[0].order_id == 9001
    assert not orders_router._idempotency_inflight


@pytest.mark.asyncio
async def test_batch_ownership_and_validation(client):
    auth = await _login(client, "own@hku.hk")
    # Order for a VPP the caller doesn't own → 404.
    r = await client.post(
        "/orders/batch",
        headers=auth,
        json={"orders": [{"vpp_id": 999999, "side": "buy", "price": 40, "qty": 0.5}]},
    )
    assert r.status_code == 404, r.text
    # Empty batch → 422.
    r = await client.post("/orders/batch", headers=auth, json={"orders": [], "cancels": []})
    assert r.status_code == 422
    # Unsupported protocol version → 400 (checked before anything else).
    r = await client.post("/orders/batch", headers=auth, json={"protocol_version": 2, "orders": []})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_batch_rate_limit_429(client):
    auth = await _login(client, "rate@hku.hk")
    vpp_id = await _make_vpp(client, auth, "rate-vpp")
    saw_429 = False
    for _ in range(20):  # 20 x 10 = 200 orders > the 120-token bucket → a 429 en route
        r = await client.post(
            "/orders/batch",
            headers=auth,
            json={
                "orders": [
                    {"vpp_id": vpp_id, "side": "sell", "price": 900, "qty": 0.01} for _ in range(10)
                ]
            },
        )
        if r.status_code == 429:
            saw_429 = True
            break
        assert r.status_code == 200, r.text
    assert saw_429, "expected a 429 after bursting past the per-account rate cap"
