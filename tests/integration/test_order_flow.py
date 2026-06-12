"""End-to-end order submission: login → create VPP → submit order → snapshot."""

from __future__ import annotations

from decimal import Decimal

import pytest


async def _logged_in_headers(client) -> dict[str, str]:
    r = await client.post("/auth/magic-link", json={"email": "trader@hku.hk"})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


@pytest.mark.asyncio
async def test_full_order_flow(client):
    headers = await _logged_in_headers(client)

    # Create a VPP.
    r = await client.post(
        "/vpps",
        headers=headers,
        json={"name": "test-vpp", "params": {"pv_kw_peak": 5.0}},
    )
    assert r.status_code in (200, 201), r.text
    vpp = r.json()
    vpp_id = vpp["id"]
    assert vpp_id >= 1
    assert vpp["name"] == "test-vpp"

    # Submit an order.
    r = await client.post(
        "/orders",
        headers=headers,
        json={"vpp_id": vpp_id, "side": "buy", "price": "80", "qty": "0.05"},
    )
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["order_id"] >= 1
    assert isinstance(order["trades"], list)
    # Remaining qty is a stringified Decimal; with the larger demo market it may partially fill.
    assert Decimal("0") <= Decimal(order["remaining_qty"]) <= Decimal("0.05")

    # Snapshot should include the order on the bid side (or it filled — both fine).
    r = await client.get("/market/snapshot?depth=5")
    assert r.status_code == 200
    snap = r.json()
    assert "bids" in snap and "asks" in snap
    assert snap["data_source"]["summary"]
    assert snap["data_source"]["sources"]


@pytest.mark.asyncio
async def test_order_bounds_rejected_with_readable_errors(client):
    headers = await _logged_in_headers(client)
    r = await client.post("/vpps", headers=headers, json={"name": "bounds-vpp", "params": {}})
    vpp_id = r.json()["id"]

    cases = [
        {"side": "buy", "price": "80", "qty": "0.001"},  # below 0.01 kWh floor
        {"side": "buy", "price": "80", "qty": "5000"},  # above 1000 kWh cap
        {"side": "sell", "price": "99999", "qty": "1"},  # above 1000 $/kWh cap
        {"side": "buy", "price": "0", "qty": "1"},  # non-positive price
    ]
    for case in cases:
        r = await client.post("/orders", headers=headers, json={"vpp_id": vpp_id, **case})
        assert r.status_code == 422, f"{case} → {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_order_for_someone_elses_vpp_is_404(client):
    headers_a = await _logged_in_headers(client)
    # Create VPP as user A
    r = await client.post("/vpps", headers=headers_a, json={"name": "alice-vpp", "params": {}})
    vpp_id = r.json()["id"]

    # Log in as user B, try to order against A's VPP
    r = await client.post("/auth/magic-link", json={"email": "bob@hku.hk"})
    tok = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": tok})
    headers_b = {"Authorization": f"Bearer {r.json()['session_token']}"}

    r = await client.post(
        "/orders",
        headers=headers_b,
        json={"vpp_id": vpp_id, "side": "sell", "price": "50", "qty": "0.1"},
    )
    assert r.status_code == 404
