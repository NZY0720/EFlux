"""Order-cancel ownership rules + the /market/participants directory."""

from __future__ import annotations

import pytest


async def _login(client, email: str) -> dict[str, str]:
    r = await client.post("/auth/magic-link", json={"email": email})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


async def _resting_order(client, headers, name: str) -> int:
    """Create a VPP and park a far-from-market bid that won't fill."""
    r = await client.post("/vpps", headers=headers, json={"name": name, "params": {}})
    vpp_id = r.json()["id"]
    r = await client.post(
        "/orders",
        headers=headers,
        json={"vpp_id": vpp_id, "side": "buy", "price": "0.5", "qty": "0.05"},
    )
    assert r.status_code == 200, r.text
    return r.json()["order_id"]


@pytest.mark.asyncio
async def test_owner_can_cancel_own_order(client):
    headers = await _login(client, "owner@hku.hk")
    order_id = await _resting_order(client, headers, "owner-vpp")

    r = await client.post("/orders/cancel", headers=headers, json={"order_id": order_id})
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_cannot_cancel_someone_elses_order(client):
    headers_a = await _login(client, "alice@hku.hk")
    order_id = await _resting_order(client, headers_a, "alice-cancel-vpp")

    headers_b = await _login(client, "bob@hku.hk")
    r = await client.post("/orders/cancel", headers=headers_b, json={"order_id": order_id})
    assert r.status_code == 404

    # Alice's order is still alive — she can cancel it herself.
    r = await client.post("/orders/cancel", headers=headers_a, json={"order_id": order_id})
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_cannot_cancel_builtin_agent_orders(client):
    """Built-in agents (negative vpp ids) place resting orders constantly; an
    authenticated user must not be able to cancel them."""
    headers = await _login(client, "mallory@hku.hk")

    # Find a builtin resting order by scanning low order ids — the simulator's
    # agents start submitting from id 1 at app startup.
    cancelled_someone = False
    for order_id in range(1, 60):
        r = await client.post("/orders/cancel", headers=headers, json={"order_id": order_id})
        assert r.status_code == 404, f"order {order_id} cancellable by non-owner: {r.status_code}"
        cancelled_someone = True
    assert cancelled_someone


@pytest.mark.asyncio
async def test_participants_directory_lists_builtin_and_external(client):
    headers = await _login(client, "carol@hku.hk")
    r = await client.post("/vpps", headers=headers, json={"name": "carol-vpp", "params": {}})
    vpp_id = r.json()["id"]

    r = await client.get("/market/participants")
    assert r.status_code == 200
    parts = r.json()
    by_id = {p["id"]: p for p in parts}

    # All builtin VPPs present (36 declared roster entries + 6 auto-spawned PPO mirrors).
    builtin = [p for p in parts if p["kind"] == "builtin"]
    assert len(builtin) == 42
    llm = next(p for p in builtin if p["name"] == "my-llm-vpp")
    assert llm["strategy"]
    assert any(p["name"].startswith("wind-") for p in builtin)
    assert any(p["name"].startswith("gas-") for p in builtin)

    # The freshly created external VPP appears too.
    assert by_id[vpp_id]["name"] == "carol-vpp"
    assert by_id[vpp_id]["kind"] == "external"
