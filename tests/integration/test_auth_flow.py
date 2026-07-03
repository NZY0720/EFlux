"""Integration test for the passwordless auth flow."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_magic_link_consume_then_session_protected_route(client):
    # 1. Request a magic link — dev env echoes the token.
    r = await client.post("/auth/magic-link", json={"email": "user@hku.hk"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] is True
    token = body["dev_token"]
    assert token and len(token) > 10

    # 2. Consume the token → get a session.
    r = await client.post("/auth/consume", json={"token": token})
    assert r.status_code == 200, r.text
    session = r.json()
    assert session["user_id"] >= 1
    assert session["email"] == "user@hku.hk"
    sess_token = session["session_token"]

    # 3. Authenticated route works.
    r = await client.get("/vpps", headers={"Authorization": f"Bearer {sess_token}"})
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)

    # 3b. A fresh user owns no managed agents yet — the house roster's LLM fleet is
    #     scoped out (owner_id=None) under per-user onboarding.
    r = await client.get("/vpps/managed", headers={"Authorization": f"Bearer {sess_token}"})
    assert r.status_code == 200, r.text
    assert r.json() == []

    # 3c. Provision a cloud-hosted managed agent (Tier 0); it then appears for this user.
    r = await client.post(
        "/vpps/managed",
        headers={"Authorization": f"Bearer {sess_token}"},
        json={
            "name": "my-managed",
            "params": {"pv_kw_peak": 4.0, "battery_kwh": 10.0},
            "persona": "Prefer maker orders; stay near 0.5 SOC.",
            "agent_params": {"demand_beta": 0.5},
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "my-managed"
    assert created["agent_kind"] == "HybridPolicyAgent"

    r = await client.get("/vpps/managed", headers={"Authorization": f"Bearer {sess_token}"})
    assert r.status_code == 200, r.text
    managed = r.json()
    assert len(managed) == 1
    assert managed[0]["name"] == "my-managed"
    assert all(m["agent_kind"] == "HybridPolicyAgent" for m in managed)

    # 3d. Its performance is queryable.
    r = await client.get(
        f"/vpps/managed/{managed[0]['id']}/performance",
        headers={"Authorization": f"Bearer {sess_token}"},
    )
    assert r.status_code == 200, r.text
    perf = r.json()
    assert "pnl" in perf
    assert "recent_trades" in perf

    # 3e. Duplicate name for the same user is rejected.
    r = await client.post(
        "/vpps/managed",
        headers={"Authorization": f"Bearer {sess_token}"},
        json={"name": "my-managed", "params": {"pv_kw_peak": 1.0}},
    )
    assert r.status_code == 409, r.text

    # 4. No auth → 401.
    r = await client.get("/vpps")
    assert r.status_code == 401

    # 5. Bad token → 401.
    r = await client.get("/vpps", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_consume_rejects_invalid_token(client):
    r = await client.post("/auth/consume", json={"token": "this-is-not-real-1234567890"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_magic_link_rate_limited_per_email(client):
    for _ in range(3):
        r = await client.post("/auth/magic-link", json={"email": "burst@hku.hk"})
        assert r.status_code == 200, r.text

    r = await client.post("/auth/magic-link", json={"email": "BURST@hku.hk"})
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_consume_rate_limited_per_ip(client):
    for i in range(20):
        r = await client.post("/auth/consume", json={"token": f"not-a-real-token-{i:02d}"})
        assert r.status_code == 400, r.text

    r = await client.post("/auth/consume", json={"token": "not-a-real-token-final"})
    assert r.status_code == 429
