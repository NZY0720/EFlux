from __future__ import annotations

from datetime import date

import pytest


async def _login(client, email: str) -> dict[str, str]:
    response = await client.post("/auth/magic-link", json={"email": email})
    assert response.status_code == 200, response.text
    response = await client.post("/auth/consume", json={"token": response.json()["dev_token"]})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['session_token']}"}


def _request(start: str = "2026-01-01", end: str = "2026-01-02") -> dict:
    return {
        "label": "January book",
        "endowment": {
            "battery": {
                "power_mw": 1,
                "energy_mwh": 2,
                "round_trip_efficiency": 0.9,
            }
        },
        "window": {"start_date": start, "end_date": end},
        "strategy": {"algorithm": "battery_arbitrageur"},
    }


@pytest.mark.asyncio
async def test_window_validation_422_includes_available_range(client, monkeypatch):
    from eflux.api.routers import proveout as router

    monkeypatch.setattr(
        router,
        "available_price_ranges",
        lambda: [(date(2026, 1, 1), date(2026, 1, 31))],
    )
    auth = await _login(client, "proveout-range@example.com")

    response = await client.post(
        "/prove-out/runs",
        headers=auth,
        json=_request("2025-12-01", "2025-12-02"),
    )

    assert response.status_code == 422, response.text
    assert "2026-01-01..2026-01-31" in response.json()["detail"]


@pytest.mark.asyncio
async def test_runs_are_owner_scoped_and_foreign_detail_is_403(client, monkeypatch):
    from eflux.api.routers import proveout as router

    monkeypatch.setattr(
        router,
        "available_price_ranges",
        lambda: [(date(2026, 1, 1), date(2026, 1, 31))],
    )
    owner = await _login(client, "proveout-owner@example.com")
    created = await client.post("/prove-out/runs", headers=owner, json=_request())
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "queued"
    run_id = created.json()["run_id"]

    owner_list = await client.get("/prove-out/runs", headers=owner)
    assert owner_list.status_code == 200, owner_list.text
    assert [row["run_id"] for row in owner_list.json()] == [run_id]

    other = await _login(client, "proveout-other@example.com")
    other_list = await client.get("/prove-out/runs", headers=other)
    assert other_list.status_code == 200, other_list.text
    assert other_list.json() == []
    denied = await client.get(f"/prove-out/runs/{run_id}", headers=other)
    assert denied.status_code == 403, denied.text

    detail = await client.get(f"/prove-out/runs/{run_id}", headers=owner)
    assert detail.status_code == 200, detail.text
    assert detail.json()["run_id"] == run_id
    assert detail.json()["report"] is None
