from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eflux.db.models import BehaviorDataset, DatasetTrainingRun, User
from eflux.ecosystem import service


async def test_training_runs_are_owner_scoped_and_newest_first(db_session) -> None:
    owner = User(email="training-list-owner@example.com")
    other = User(email="training-list-other@example.com")
    db_session.add_all([owner, other])
    await db_session.flush()
    dataset = BehaviorDataset(
        owner_id=owner.id,
        name="persisted-runs",
        version="1",
        market="realprice",
        visibility="private",
        status="published",
        manifest={},
    )
    db_session.add(dataset)
    await db_session.flush()
    now = datetime(2026, 7, 14, tzinfo=UTC)
    older = DatasetTrainingRun(
        dataset_id=dataset.id,
        owner_id=owner.id,
        algorithm="bc_warm_start",
        status="succeeded",
        config={},
        metrics={},
        created_at=now - timedelta(hours=1),
    )
    newer = DatasetTrainingRun(
        dataset_id=dataset.id,
        owner_id=owner.id,
        algorithm="ppo_finetune",
        status="queued",
        config={},
        metrics={},
        created_at=now,
    )
    db_session.add_all([older, newer])
    await db_session.flush()

    rows = await service.list_dataset_training_runs(db_session, dataset.id, owner)

    assert [row.id for row in rows] == [newer.id, older.id]
    with pytest.raises(service.EcosystemError) as error:
        await service.list_dataset_training_runs(db_session, dataset.id, other)
    assert error.value.status_code == 404
