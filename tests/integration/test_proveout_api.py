from __future__ import annotations

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
            },
            "solar_mw": 1.5,
            "wind": {"power_mw": 2, "mean_speed_mps": 7.5},
            "load": {"base_mw": 0.8, "profile": "commercial", "flexibility": 0.25},
            "cash_usd": 100000,
        },
        "window": {"start_date": start, "end_date": end},
        "strategy": {"algorithm": "battery_arbitrageur"},
    }


@pytest.mark.asyncio
async def test_uncached_window_is_queued_for_background_data_preparation(client):
    auth = await _login(client, "proveout-range@example.com")

    response = await client.post(
        "/prove-out/runs",
        headers=auth,
        json=_request("2026-01-01", "2026-01-02"),
    )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_runs_are_owner_scoped_and_foreign_detail_is_403(client):
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
    assert detail.json()["endowment"]["wind"]["power_mw"] == 2
    assert detail.json()["endowment"]["load"]["profile"] == "commercial"
