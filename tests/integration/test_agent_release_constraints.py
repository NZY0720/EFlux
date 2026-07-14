from __future__ import annotations

from decimal import Decimal

import pytest

from eflux.db.models import VPP, ReleaseEvaluation


async def _login(client, email: str = "release-owner@hku.hk") -> dict[str, str]:
    response = await client.post("/auth/magic-link", json={"email": email})
    token = response.json()["dev_token"]
    response = await client.post("/auth/consume", json={"token": token})
    return {"Authorization": f"Bearer {response.json()['session_token']}"}


def _release_payload(*, name: str = "Strict Release") -> dict:
    return {
        "name": name,
        "version": "1",
        "description": "A complete, platform-managed release contract.",
        "market": "p2p",
        "visibility": "private",
        "badges": [],
        "recipe": {
            "algorithm": "truthful",
            "agent_params": {},
            "protocol_version": "1",
            "observation_schema_version": "1",
            "action_schema_version": "1",
            "online_learning": False,
            "fallback_strategy": "safe_hold",
            "risk_limits": {
                "max_open_orders": 256,
                "max_new_orders_per_decision": 20,
                "credit_limit_usd": 1000,
            },
            "order_routing": {"markets": ["p2p"], "default_route": "auto"},
        },
        "state": {},
        "compatibility": {
            "market": "p2p",
            "profile_id": "battery-only",
            "minimum_cash_usd": 0,
            "minimum_credit_usd": 1000,
        },
        "environment": {
            "runtime": "eflux-managed",
            "agent_protocol_version": 1,
            "dependencies_locked": True,
            "git_commit": "abcdef0",
        },
    }


async def _published_release(client, auth: dict[str, str], *, name: str = "Strict Release"):
    response = await client.post("/agent-releases", headers=auth, json=_release_payload(name=name))
    assert response.status_code == 201, response.text
    release_id = response.json()["id"]
    response = await client.post(f"/agent-releases/{release_id}/publish", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_publish_rejects_incomplete_contract_and_reserved_evidence(client):
    auth = await _login(client)
    incomplete = _release_payload()
    incomplete["recipe"] = {}
    response = await client.post("/agent-releases", headers=auth, json=incomplete)
    assert response.status_code == 201, response.text
    response = await client.post(f"/agent-releases/{response.json()['id']}/publish", headers=auth)
    assert response.status_code == 422
    assert "recipe" in response.json()["detail"]

    self_badged = _release_payload(name="Self Badged")
    self_badged["badges"] = ["Verified Live"]
    response = await client.post("/agent-releases", headers=auth, json=self_badged)
    assert response.status_code == 422
    assert "cannot be self-assigned" in response.json()["detail"]


@pytest.mark.asyncio
async def test_evaluation_provenance_is_platform_owned(client):
    auth = await _login(client)
    release = await _published_release(client, auth)
    response = await client.post(
        f"/agent-releases/{release['id']}/evaluations",
        headers=auth,
        json={
            "kind": "p2p_tournament",
            "config": {},
            "provenance": "self_reported",
        },
    )
    assert response.status_code == 422

    response = await client.post(
        f"/agent-releases/{release['id']}/evaluations",
        headers=auth,
        json={"kind": "p2p_tournament", "config": {}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["provenance"] == "platform_verified"


@pytest.mark.asyncio
async def test_shadow_promotes_in_place_only_after_evidence_and_acknowledgement(client, db_session):
    auth = await _login(client)
    release = await _published_release(client, auth)
    response = await client.post(
        f"/agent-releases/{release['id']}/evaluations",
        headers=auth,
        json={"kind": "p2p_tournament", "config": {}},
    )
    assert response.status_code == 201, response.text
    evaluation_id = response.json()["id"]

    response = await client.post(
        f"/agent-releases/{release['id']}/deploy",
        headers=auth,
        json={"name": "paper-instance", "profile_id": "battery-only", "mode": "paper"},
    )
    assert response.status_code == 201, response.text
    deployment = response.json()
    simulator = client._transport.app.state.simulator
    runtime = next(
        vpp for vpp in simulator.my_managed_vpps() if vpp.managed_def_id == deployment["id"]
    )
    state_identity = id(runtime.state)
    agent_identity = id(runtime.agent)
    runtime.state.pnl = Decimal("12.34")
    runtime.state.pending_net_kwh = 7.5

    response = await client.post(
        f"/agent-deployments/{deployment['id']}/promote-live",
        headers=auth,
        json={"risk_acknowledged": True},
    )
    assert response.status_code == 409

    evaluation = await db_session.get(ReleaseEvaluation, evaluation_id)
    assert evaluation is not None
    evaluation.status = "done"
    evaluation.metrics = {"test": True}
    await db_session.commit()

    response = await client.post(
        f"/agent-deployments/{deployment['id']}/promote-live",
        headers=auth,
        json={"risk_acknowledged": False},
    )
    assert response.status_code == 422
    response = await client.post(
        f"/agent-deployments/{deployment['id']}/promote-live",
        headers=auth,
        json={"risk_acknowledged": True},
    )
    assert response.status_code == 200, response.text
    promoted = response.json()
    assert promoted["mode"] == "live"
    assert promoted["release_id"] == release["id"]
    assert promoted["release_content_sha256"] == release["content_sha256"]
    assert id(runtime.state) == state_identity
    assert id(runtime.agent) == agent_identity
    assert runtime.state.pnl == Decimal("12.34")
    assert runtime.state.pending_net_kwh == 7.5

    row = await db_session.get(VPP, deployment["id"])
    await db_session.refresh(row)
    assert row is not None
    assert row.managed_config["deployment_mode"] == "live"
    assert row.release_id == release["id"]
    assert row.release_content_sha256 == release["content_sha256"]

    response = await client.patch(
        f"/vpps/managed/{deployment['id']}",
        headers=auth,
        json={"params": {"battery_kwh": 50}},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_two_release_deployments_have_independent_runtime_state(client):
    auth = await _login(client)
    release = await _published_release(client, auth)
    first = await client.post(
        f"/agent-releases/{release['id']}/deploy",
        headers=auth,
        json={"name": "shadow-one", "mode": "shadow"},
    )
    second = await client.post(
        f"/agent-releases/{release['id']}/deploy",
        headers=auth,
        json={"name": "shadow-two", "mode": "shadow"},
    )
    assert first.status_code == second.status_code == 201
    simulator = client._transport.app.state.simulator
    runtimes = {
        runtime.managed_def_id: runtime
        for runtime in simulator.my_managed_vpps()
        if runtime.managed_def_id in {first.json()["id"], second.json()["id"]}
    }
    one = runtimes[first.json()["id"]]
    two = runtimes[second.json()["id"]]
    assert one.vpp_id != two.vpp_id
    assert one.state is not two.state
    assert one.agent is not two.agent
    one.state.pending_net_kwh = 99.0
    assert two.state.pending_net_kwh != 99.0
