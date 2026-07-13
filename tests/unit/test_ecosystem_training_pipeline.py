from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from eflux.agents.ppo.primitive_encoding import (
    ACTION_PROFILE_REALPRICE_GRID,
    ENCODING_V2,
    OBS_DIM_V4,
    OBS_V4,
    action_dim,
)
from eflux.db import Base
from eflux.db.models import AgentRelease, User
from eflux.ecosystem import runtime, service, worker

pytest.importorskip("torch")


def _record(index: int) -> dict:
    action_width = action_dim(
        ENCODING_V2,
        action_profile=ACTION_PROFILE_REALPRICE_GRID,
    )
    return {
        "schema_version": "1",
        "decision_id": f"decision-{index}",
        "participant_id": -7,
        "sim_ts": f"2026-07-01T00:{index:02d}:00+00:00",
        "observation": {
            "market": {"interval_id": "interval-1", "market_mode": "realprice"},
            "portfolio": {"soc_kwh": 50.0},
        },
        "action": {
            "rationale": "hold",
            "orders": [],
            "cancels": [],
            "replaces": [],
            "is_noop": True,
            "policy_sample": {
                "encoding_version": ENCODING_V2,
                "observation_version": OBS_V4,
                "action_profile": ACTION_PROFILE_REALPRICE_GRID,
                "observation_vector": [float(index)] * OBS_DIM_V4,
                "action_vector": [1.0, *([-1.0] * (action_width - 1))],
                "mode": "noop",
            },
        },
        "execution_result": {
            "accepted_order_ids": [],
            "cancelled_order_ids": [],
            "rejections": [],
            "fills": [],
            "unfilled_order_count": 0,
            "slippage_usd": "0",
            "fallback": False,
        },
        "outcome": {"imbalance_kwh": 0.0, "economic_delta_usd": "0"},
        "next_observation": None,
    }


@pytest.mark.asyncio
async def test_published_trajectory_to_bc_to_publishable_derived_release(
    tmp_path, monkeypatch
) -> None:
    project = tmp_path / "project"
    dataset_base = project / "artifacts" / "behavior_datasets"
    training_base = project / "artifacts" / "training_runs"
    dataset_base.mkdir(parents=True)
    training_base.mkdir(parents=True)
    artifact = dataset_base / "owner" / "trajectory.jsonl.gz"
    artifact.parent.mkdir(parents=True)
    with gzip.open(artifact, "wt", encoding="utf-8") as handle:
        for index in (1, 2):
            handle.write(json.dumps(_record(index), sort_keys=True))
            handle.write("\n")

    monkeypatch.setattr(service, "PROJECT_ROOT", project)
    monkeypatch.setattr(service, "DATASET_ARTIFACTS_BASE", dataset_base)
    monkeypatch.setattr(worker, "PROJECT_ROOT", project)
    monkeypatch.setattr(worker, "TRAINING_ARTIFACTS_BASE", training_base)
    monkeypatch.setattr(runtime, "PROJECT_ROOT", project)
    monkeypatch.setattr(runtime, "_CHECKPOINT_ROOTS", (training_base,))

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/training.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            owner = User(email="pipeline@example.com")
            session.add(owner)
            await session.flush()
            dataset = await service.create_behavior_dataset(
                session,
                owner,
                {
                    "name": "trajectory",
                    "version": "1",
                    "market": "realprice",
                    "visibility": "private",
                    "schema_version": "1",
                    "manifest": {"provenance": "self_reported"},
                    "artifact_path": "owner/trajectory.jsonl.gz",
                    "license": "EFlux-Research-1.0",
                },
            )
            await service.publish_behavior_dataset(session, dataset.id, owner)
            run = await service.create_dataset_training_run(
                session,
                dataset.id,
                owner,
                {
                    "algorithm": "bc_warm_start",
                    "config": {
                        "epochs": 1,
                        "seed": 7,
                        "git_commit": "a" * 40,
                    },
                },
            )
            owner_id = owner.id
            run_id = run.id
            await session.commit()

        job = await worker.claim_next_ecosystem_job(sessions)
        assert job is not None and job.kind == "training" and job.id == run_id
        await worker.execute_ecosystem_job(job, sessions)

        async with sessions() as session:
            run = await service.get_dataset_training_run(
                session, run_id, User(id=owner_id, email="pipeline@example.com")
            )
            assert run.status == "succeeded"
            release = await session.get(AgentRelease, run.output_release_id)
            assert release is not None
            assert release.recipe["training_method"] == "bc_warm_start"
            assert release.recipe["risk_limits"]["max_open_orders"] == 20
            assert release.environment["git_commit"] == "a" * 40
            service.validate_agent_release_for_publish(release)
            checkpoint = Path(project / release.state["checkpoint_path"])
            assert checkpoint.is_file()
            agent = runtime.agent_factory_from_release(release, learning=False)
            assert agent is not None
    finally:
        await engine.dispose()
