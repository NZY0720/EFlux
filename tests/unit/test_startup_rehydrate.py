from __future__ import annotations

from types import SimpleNamespace

from eflux.api import main as api_main
from eflux.db.models import VPP, AgentRelease, User


async def test_release_checkpoint_failure_is_persisted_and_not_provisioned(
    db_session, monkeypatch
) -> None:
    owner = User(email="rehydrate-failure@example.com")
    db_session.add(owner)
    await db_session.flush()
    release = AgentRelease(
        owner_id=owner.id,
        name="missing-checkpoint",
        version="1",
        market="realprice",
        visibility="private",
        status="published",
        recipe={"algorithm": "ppo"},
        state={
            "checkpoint_path": "artifacts/training_runs/missing.pt",
            "checkpoint_sha256": "c" * 64,
        },
        compatibility={},
        environment={},
        badges=[],
        content_sha256="d" * 64,
    )
    db_session.add(release)
    await db_session.flush()
    deployment = VPP(
        owner_id=owner.id,
        name="failed-deployment",
        params={},
        is_external=True,
        is_managed=True,
        managed_config={
            "algorithm": "ppo",
            "llm_enabled": False,
            "online_learning": False,
            "deployment_mode": "live",
        },
        release_id=release.id,
        release_content_sha256=release.content_sha256,
    )
    db_session.add(deployment)
    await db_session.commit()

    def unexpected_provision(*args, **kwargs):
        raise AssertionError("corrupt release must not be provisioned")

    monkeypatch.setattr(api_main, "provision_managed_vpp", unexpected_provision)
    await api_main._rehydrate_managed_vpps(SimpleNamespace(market_mode="realprice"))

    await db_session.refresh(deployment)
    config = dict(deployment.managed_config or {})
    assert config["deployment_status"] == "failed"
    assert "checkpoint" in config["deployment_error"].lower()
