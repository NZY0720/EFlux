from __future__ import annotations

import pytest


async def _login(client, email: str) -> dict[str, str]:
    response = await client.post("/auth/magic-link", json={"email": email})
    response = await client.post("/auth/consume", json={"token": response.json()["dev_token"]})
    return {"Authorization": f"Bearer {response.json()['session_token']}"}


@pytest.mark.asyncio
async def test_builtin_agent_example_is_public_evaluated_and_forkable(client):
    response = await client.get("/agent-releases")
    assert response.status_code == 200, response.text
    examples = [row for row in response.json() if "Built-in Example" in row["badges"]]
    assert len(examples) == 1
    release = examples[0]
    assert release["name"] == "Battery Shift Starter"
    assert release["visibility"] == "public"
    assert release["status"] == "verified"

    response = await client.get(f"/agent-releases/{release['id']}/evaluations")
    assert response.status_code == 200, response.text
    evaluations = response.json()
    assert len(evaluations) == 1
    assert evaluations[0]["status"] == "done"
    assert evaluations[0]["provenance"] == "platform_verified"
    assert evaluations[0]["evidence_sha256"]

    auth = await _login(client, "example-forker@example.com")
    response = await client.post(
        f"/agent-releases/{release['id']}/fork",
        headers=auth,
        json={"name": "My Battery Shift", "version": "0.1.0", "visibility": "private"},
    )
    assert response.status_code == 201, response.text
    fork = response.json()
    assert fork["parent_release_id"] == release["id"]
    assert fork["status"] == "draft"
    assert fork["recipe"] == release["recipe"]
