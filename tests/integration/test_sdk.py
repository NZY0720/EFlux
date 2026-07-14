"""EFlux Python SDK — exercised in-process against the ASGI app."""

from __future__ import annotations

import pytest

from eflux.sdk import EFluxClient, EFluxError, Order


@pytest.mark.asyncio
async def test_sdk_end_to_end(client):
    # Reuse the in-process ASGI client so the SDK talks to the live app without a socket.
    sdk = EFluxClient(http=client)

    await sdk.login_dev("sdk@hku.hk")
    assert sdk.token

    vpp = await sdk.create_vpp("sdk-bot", {"pv_kw_peak": 4.0, "battery_kwh": 10.0})
    vpp_id = vpp["id"]

    snap = await sdk.market_snapshot()
    assert "best_bid" in snap
    product = next(row for row in await sdk.products() if row["is_open"])
    product_id = product["product_id"]

    # Batch submit — two sells priced high so they rest.
    res = await sdk.submit_batch(
        [
            Order(vpp_id, "sell", 900, 0.05, product_id, "battery", client_ref="a"),
            Order(vpp_id, "sell", 910, 0.05, product_id, "battery", client_ref="b"),
        ],
        idempotency_key="k1",
    )
    assert res["protocol_version"] == 1
    ids = [r["order_id"] for r in res["results"] if r["status"] == "accepted"]
    assert len(ids) == 2

    # Idempotency replay through the SDK — same key, same result, no new orders.
    res2 = await sdk.submit_batch(
        [
            Order(vpp_id, "sell", 900, 0.05, product_id, "battery", client_ref="a"),
            Order(vpp_id, "sell", 910, 0.05, product_id, "battery", client_ref="b"),
        ],
        idempotency_key="k1",
    )
    assert [r["order_id"] for r in res2["results"]] == [r["order_id"] for r in res["results"]]

    # State read.
    opens = await sdk.open_orders(vpp_id)
    assert {o["order_id"] for o in opens} >= set(ids)

    # Cancel via batch.
    res3 = await sdk.submit_batch(cancels=ids)
    assert all(c["ok"] for c in res3["cancelled"])
    assert await sdk.open_orders(vpp_id) == []

    # Errors surface as EFluxError with the server's status + detail.
    with pytest.raises(EFluxError) as ei:
        await sdk.submit_batch([Order(999999, "buy", 40, 0.05, product_id, "balance")])
    assert ei.value.status_code == 404
