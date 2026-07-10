"""Integration test for the passwordless auth flow."""

from __future__ import annotations

import pytest
from sqlalchemy import select


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
    client.cookies.clear()
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
async def test_consume_sets_cookie_and_cookie_authenticates_me(client):
    r = await client.post("/auth/magic-link", json={"email": "cookie-user@hku.hk"})
    r = await client.post("/auth/consume", json={"token": r.json()["dev_token"]})
    assert r.status_code == 200, r.text
    set_cookie = r.headers["set-cookie"].lower()
    assert "eflux_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "max-age=2592000" in set_cookie
    assert "secure" not in set_cookie

    r = await client.get("/auth/me")
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "cookie-user@hku.hk"


@pytest.mark.asyncio
async def test_cookie_logout_clears_cookie_and_invalidates_session(client):
    r = await client.post("/auth/magic-link", json={"email": "cookie-logout@hku.hk"})
    r = await client.post("/auth/consume", json={"token": r.json()["dev_token"]})
    session_token = r.json()["session_token"]

    r = await client.post("/auth/logout")
    assert r.status_code == 204, r.text
    assert "eflux_session=\"\"" in r.headers["set-cookie"].lower()
    assert "max-age=0" in r.headers["set-cookie"].lower()

    r = await client.get("/auth/me")
    assert r.status_code == 401, r.text
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {session_token}"})
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_me_reports_default_role_and_logout_invalidates_session(client):
    r = await client.post("/auth/magic-link", json={"email": "role-default@hku.hk"})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    session = r.json()
    headers = {"Authorization": f"Bearer {session['session_token']}"}

    r = await client.get("/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "id": session["user_id"],
        "email": "role-default@hku.hk",
        "role": "user",
    }

    r = await client.post("/auth/logout", headers=headers)
    assert r.status_code == 204, r.text

    r = await client.get("/auth/me", headers=headers)
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_magic_link_consume_promotes_configured_admin(monkeypatch, client, db_session):
    monkeypatch.setenv("EFLUX_ADMIN_EMAILS", "admin-list@hku.hk")
    from eflux.config import get_settings

    get_settings.cache_clear()
    r = await client.post("/auth/magic-link", json={"email": "ADMIN-LIST@hku.hk"})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    session = r.json()
    headers = {"Authorization": f"Bearer {session['session_token']}"}

    r = await client.get("/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"

    from eflux.db.models import User

    user = (await db_session.execute(select(User).where(User.id == session["user_id"]))).scalar_one()
    assert user.role == "admin"


@pytest.mark.asyncio
async def test_ppo_renew_requires_admin_and_allows_configured_admin(monkeypatch, client):
    monkeypatch.setenv("EFLUX_ADMIN_EMAILS", "ppo-admin@hku.hk")
    from eflux.config import get_settings
    from eflux.simulator.runner import Simulator

    get_settings.cache_clear()
    monkeypatch.setattr(Simulator, "start_ppo_renew", lambda self, days: True)

    r = await client.post("/auth/magic-link", json={"email": "ppo-user@hku.hk"})
    r = await client.post("/auth/consume", json={"token": r.json()["dev_token"]})
    user_headers = {"Authorization": f"Bearer {r.json()['session_token']}"}
    r = await client.post("/market/ppo/renew", headers=user_headers)
    assert r.status_code == 403, r.text

    r = await client.post("/auth/magic-link", json={"email": "ppo-admin@hku.hk"})
    r = await client.post("/auth/consume", json={"token": r.json()["dev_token"]})
    admin_headers = {"Authorization": f"Bearer {r.json()['session_token']}"}
    r = await client.post("/market/ppo/renew", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "started"


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
