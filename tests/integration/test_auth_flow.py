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

    # 3b. The LLM fleet is exposed as managed My VPPs.
    r = await client.get("/vpps/managed", headers={"Authorization": f"Bearer {sess_token}"})
    assert r.status_code == 200, r.text
    managed = r.json()
    assert len(managed) == 6
    assert managed[0]["name"] == "my-llm-vpp"
    assert all(m["agent_kind"] == "HybridPolicyAgent" for m in managed)
    r = await client.get(
        f"/vpps/managed/{managed[0]['id']}/performance",
        headers={"Authorization": f"Bearer {sess_token}"},
    )
    assert r.status_code == 200, r.text
    perf = r.json()
    assert "pnl" in perf
    assert "recent_trades" in perf

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
