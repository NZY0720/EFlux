from __future__ import annotations

import pytest


async def _headers(client) -> dict[str, str]:
    magic = await client.post("/auth/magic-link", json={"email": "v2-agent@example.com"})
    token = magic.json()["dev_token"]
    session = await client.post("/auth/consume", json={"token": token})
    return {"Authorization": f"Bearer {session.json()['session_token']}"}


@pytest.mark.asyncio
async def test_external_protocol_v2_requires_product_and_physical_purpose(client):
    headers = await _headers(client)
    products = (await client.get("/market/products")).json()
    assert products and products[0]["is_open"]
    product_id = products[0]["product_id"]

    created = await client.post(
        "/vpps",
        headers=headers,
        json={
            "name": "v2-external",
            "params": {
                "pv_kw_peak": 0,
                "battery_kwh": 2,
                "battery_kw_max": 4,
                "load_kw_base": 0,
            },
        },
    )
    assert created.status_code == 201, created.text
    vpp_id = created.json()["id"]

    submitted = await client.post(
        "/orders",
        headers=headers,
        json={
            "vpp_id": vpp_id,
            "side": "sell",
            "price": "999",
            "qty_kwh": "0.05",
            "product_id": product_id,
            "purpose": "battery",
            "time_in_force": "good_til_gate",
            "ttl_sec": 20,
        },
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["product_id"] == product_id
    assert body["remaining_qty"] == "0.05"

    opened = await client.get("/orders/open", headers=headers, params={"vpp_id": vpp_id})
    assert opened.status_code == 200
    assert opened.json()[0]["purpose"] == "battery"
    assert opened.json()[0]["product_id"] == product_id

    cancelled = await client.post(
        "/orders/cancel", headers=headers, json={"order_id": body["order_id"]}
    )
    assert cancelled.status_code == 204


@pytest.mark.asyncio
async def test_protocol_v2_batch_echoes_client_reference(client):
    headers = await _headers(client)
    product_id = (await client.get("/market/products")).json()[0]["product_id"]
    created = await client.post(
        "/vpps",
        headers=headers,
        json={
            "name": "v2-batch",
            "params": {
                "pv_kw_peak": 0,
                "battery_kwh": 2,
                "battery_kw_max": 4,
                "load_kw_base": 0,
            },
        },
    )
    vpp_id = created.json()["id"]
    response = await client.post(
        "/orders/batch",
        headers=headers,
        json={
            "protocol_version": 2,
            "idempotency_key": "v2-batch-1",
            "orders": [
                {
                    "vpp_id": vpp_id,
                    "side": "buy",
                    "price": "-25",
                    "qty_kwh": "0.05",
                    "product_id": product_id,
                    "purpose": "battery",
                    "client_ref": "quote-a",
                }
            ],
            "cancels": [],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["protocol_version"] == 2
    assert body["results"][0]["client_ref"] == "quote-a"
    assert body["results"][0]["status"] == "accepted"
