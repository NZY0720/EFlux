"""Integration tests for managed-agent chatroom presence: owner speaking through the
agent (POST /vpps/managed/{id}/say), chat prefs (PUT /vpps/managed/{id}/chat), and the
public chatter feed carrying color/avatar/source."""

from __future__ import annotations

import pytest

from eflux.api.main import _rehydrate_managed_vpps
from eflux.bridge import InMemoryBus
from eflux.simulator.runner import Simulator

pytestmark = pytest.mark.asyncio


async def _login(client, email="chatty@hku.hk") -> dict:
    r = await client.post("/auth/magic-link", json={"email": email})
    token = r.json()["dev_token"]
    r = await client.post("/auth/consume", json={"token": token})
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


async def _managed(client, auth, name="chatty-agent") -> int:
    r = await client.post(
        "/vpps/managed",
        headers=auth,
        json={"name": name, "params": {"pv_kw_peak": 2.0, "battery_kwh": 5.0}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_owner_speaks_and_room_shows_presence(client):
    auth = await _login(client)
    managed_id = await _managed(client, auth)

    # Set presence first: voice + color + avatar (display-only, no restart).
    r = await client.put(
        f"/vpps/managed/{managed_id}/chat",
        headers=auth,
        json={"style": "dry one-liners", "color": "#e11d48", "avatar": "🔋"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chat_style"] == "dry one-liners"
    assert body["chat_color"] == "#e11d48"
    assert body["chat_avatar"] == "🔋"

    # Speak as the agent; the line lands in the public room with presence + source.
    r = await client.post(
        f"/vpps/managed/{managed_id}/say",
        headers=auth,
        json={"text": "  buying the dip, literally  "},
    )
    assert r.status_code == 200, r.text
    posted = r.json()
    assert posted["name"] == "chatty-agent"
    assert posted["text"] == "buying the dip, literally"  # cleaned/normalized
    assert posted["source"] == "owner"
    assert posted["color"] == "#e11d48"

    r = await client.get("/market/chatter")
    msgs = r.json()
    mine = next(m for m in msgs if m["name"] == "chatty-agent")
    assert mine["source"] == "owner"
    assert mine["avatar"] == "🔋"


async def test_say_requires_ownership_and_validates(client):
    auth_a = await _login(client, "owner-a@hku.hk")
    managed_id = await _managed(client, auth_a, name="a-agent")
    auth_b = await _login(client, "owner-b@hku.hk")

    r = await client.post(
        f"/vpps/managed/{managed_id}/say", headers=auth_b, json={"text": "impostor"}
    )
    assert r.status_code == 404
    # A message that is empty after cleanup is rejected.
    r = await client.post(
        f"/vpps/managed/{managed_id}/say", headers=auth_a, json={"text": "  '' "}
    )
    assert r.status_code == 422
    # Bad color is rejected by validation.
    r = await client.put(
        f"/vpps/managed/{managed_id}/chat", headers=auth_a, json={"color": "red"}
    )
    assert r.status_code == 422


async def test_say_rate_limited(client):
    auth = await _login(client)
    managed_id = await _managed(client, auth)
    statuses = []
    for i in range(6):
        r = await client.post(
            f"/vpps/managed/{managed_id}/say", headers=auth, json={"text": f"line {i}"}
        )
        statuses.append(r.status_code)
    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429


async def test_chat_prefs_survive_restart_and_patch(client):
    auth = await _login(client)
    managed_id = await _managed(client, auth)
    r = await client.put(
        f"/vpps/managed/{managed_id}/chat",
        headers=auth,
        json={"style": "haiku only", "color": "#0284c7", "avatar": "🌊"},
    )
    assert r.status_code == 200

    # A preferences PATCH (re-provision) must not drop chat prefs (merge, not rewrite).
    r = await client.patch(
        f"/vpps/managed/{managed_id}", headers=auth, json={"persona": "New brief."}
    )
    assert r.status_code == 200, r.text
    assert r.json()["chat_avatar"] == "🌊"

    # And a full restart rehydrates them onto the fresh agent.
    fresh = Simulator(bus=InMemoryBus())
    await _rehydrate_managed_vpps(fresh)
    vpp = next(v for v in fresh.vpps.values() if v.managed_def_id == managed_id)
    assert vpp.chat_style == "haiku only"
    assert vpp.chat_color == "#0284c7"
    assert vpp.chat_avatar == "🌊"
