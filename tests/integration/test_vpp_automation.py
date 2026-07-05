"""Self-created (passive) VPPs: full-endowment create, delete, and the API-key surface that
lets an external app drive them (Tier A1)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _login(client, email="automation@hku.hk") -> dict:
    r = await client.post("/auth/magic-link", json={"email": email})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


async def test_passive_vpp_full_endowment_create_and_delete(client):
    auth = await _login(client)
    r = await client.post(
        "/vpps",
        headers=auth,
        json={
            "name": "bot-vpp",
            "params": {
                "pv_kw_peak": 4.0,
                "battery_kwh": 10.0,
                "load_kw_base": 2.0,
                "wind_kw_rated": 3.0,
                "load_profile": "commercial",
            },
        },
    )
    assert r.status_code == 201, r.text
    vpp_id = r.json()["id"]
    # The full endowment round-trips (parity with the managed deploy form).
    assert r.json()["params"]["wind_kw_rated"] == 3.0
    assert r.json()["params"]["load_profile"] == "commercial"

    r = await client.get("/vpps", headers=auth)
    assert any(v["id"] == vpp_id and v["is_active"] for v in r.json())

    # Delete → the VPP disappears from the list entirely (no inactive rows shown).
    r = await client.delete(f"/vpps/{vpp_id}", headers=auth)
    assert r.status_code == 204, r.text
    r = await client.get("/vpps", headers=auth)
    assert not any(v["id"] == vpp_id for v in r.json())


async def test_api_key_mint_list_revoke(client):
    auth = await _login(client, "keys@hku.hk")
    r = await client.post("/auth/api-keys", headers=auth, json={"name": "market-maker"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] and body["prefix"]  # plaintext shown once
    prefix = body["prefix"]

    # Listed with metadata only — never the plaintext.
    r = await client.get("/auth/api-keys", headers=auth)
    assert r.status_code == 200, r.text
    keys = r.json()
    assert any(k["prefix"] == prefix and "key" not in k for k in keys)

    # Revoke → 204, then it shows as revoked; revoking again is a 404.
    r = await client.delete(f"/auth/api-keys/{prefix}", headers=auth)
    assert r.status_code == 204, r.text
    r = await client.get("/auth/api-keys", headers=auth)
    assert any(k["prefix"] == prefix and k["revoked_at"] for k in r.json())
    r = await client.delete(f"/auth/api-keys/{prefix}", headers=auth)
    assert r.status_code == 404, r.text
