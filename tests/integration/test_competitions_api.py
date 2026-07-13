"""Integration coverage for the public competition catalogue."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select


async def _login(client, email: str) -> dict[str, str]:
    response = await client.post("/auth/magic-link", json={"email": email})
    assert response.status_code == 200, response.text
    response = await client.post("/auth/consume", json={"token": response.json()["dev_token"]})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['session_token']}"}


def _submission_body(**payload_overrides: object) -> dict:
    payload = {
        "algorithm": "ppo",
        "llm_enabled": False,
        "preset": "Solar Trader",
    }
    payload.update(payload_overrides)
    return {"track": "managed", "payload": payload}


@pytest.mark.asyncio
async def test_default_competition_seed_is_idempotent(db_session):
    from eflux.api.main import _seed_default_competition
    from eflux.db.models import AuditEvent, Competition, CompetitionRuleSet

    await _seed_default_competition()
    await _seed_default_competition()

    assert await db_session.scalar(select(func.count(Competition.id))) == 1
    competition = (await db_session.execute(select(Competition))).scalar_one()
    assert competition.slug == "season-0"
    assert competition.status == "open"
    ruleset = (await db_session.execute(select(CompetitionRuleSet))).scalar_one()
    assert ruleset.track == "managed"
    assert ruleset.version == "rules-v1.1"
    assert ruleset.config == {
        "window_sec": 300,
        "deadline_ms": 500,
        "practice_seeds": 3,
        "hidden_seeds": 5,
        "holdout_seeds": 2,
        "submissions_per_day": 2,
        "seed_hours": 24,
    }
    assert await db_session.scalar(
        select(func.count(AuditEvent.id)).where(AuditEvent.action == "competition.seeded")
    ) == 1


@pytest.mark.asyncio
async def test_competition_list_and_detail_are_public(client):
    r = await client.get("/competitions")
    assert r.status_code == 200, r.text
    assert r.json() == [
        {
            "id": 1,
            "slug": "season-0",
            "title": "EFlux Open — Season 0",
            "status": "open",
            "tracks": ["managed"],
            "submission_counts": {},
        }
    ]

    r = await client.get("/competitions/season-0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == ""
    assert body["tracks"] == ["managed"]
    assert body["submission_counts"] == {}
    assert len(body["rulesets"]) == 1
    assert body["rulesets"][0]["track"] == "managed"
    assert body["rulesets"][0]["version"] == "rules-v1.1"
    assert body["rulesets"][0]["config"]["window_sec"] == 300


@pytest.mark.asyncio
async def test_competition_detail_returns_404_for_unknown_slug(client):
    r = await client.get("/competitions/unknown-season")
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_competition_read_apis_count_submissions(client, db_session):
    from eflux.db.models import Competition, Submission, User

    competition = (await db_session.execute(select(Competition))).scalar_one()
    user = User(email="competition-count@hku.hk")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Submission(
            competition_id=competition.id,
            user_id=user.id,
            track="managed",
            status="draft",
            payload={"managed_vpp_id": 7},
        )
    )
    await db_session.commit()

    r = await client.get("/competitions")
    assert r.status_code == 200, r.text
    assert r.json()[0]["submission_counts"] == {"managed": 1}

    r = await client.get("/competitions/season-0")
    assert r.status_code == 200, r.text
    assert r.json()["submission_counts"] == {"managed": 1}


@pytest.mark.asyncio
async def test_managed_submission_validates_algorithm_llm_and_competition_state(client, db_session):
    from eflux.db.models import AuditEvent, Competition

    auth = await _login(client, "competition-submit-validation@hku.hk")
    r = await client.post(
        "/competitions/season-0/submissions",
        headers=auth,
        json=_submission_body(algorithm="not-an-algorithm"),
    )
    assert r.status_code == 422, r.text
    assert "unknown managed algorithm" in r.json()["detail"]

    r = await client.post(
        "/competitions/season-0/submissions",
        headers=auth,
        json=_submission_body(llm_enabled=True),
    )
    assert r.status_code == 422, r.text
    assert "live sandbox" in r.json()["detail"]

    r = await client.post("/competitions/season-0/submissions", headers=auth, json=_submission_body())
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "finalized"
    assert await db_session.scalar(
        select(func.count(AuditEvent.id)).where(AuditEvent.action == "submission.created")
    ) == 1

    competition = (await db_session.execute(select(Competition))).scalar_one()
    competition.status = "closed"
    await db_session.commit()
    r = await client.post("/competitions/season-0/submissions", headers=auth, json=_submission_body())
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_submission_cooldown_and_evaluation_seed_queue_and_owner_access(client, db_session):
    from eflux.db.models import AuditEvent, EvaluationSeedRun, User

    owner_auth = await _login(client, "competition-owner@hku.hk")
    first = await client.post(
        "/competitions/season-0/submissions", headers=owner_auth, json=_submission_body()
    )
    assert first.status_code == 201, first.text
    submission_id = first.json()["id"]
    second = await client.post(
        "/competitions/season-0/submissions", headers=owner_auth, json=_submission_body()
    )
    assert second.status_code == 201, second.text
    blocked = await client.post(
        "/competitions/season-0/submissions", headers=owner_auth, json=_submission_body()
    )
    assert blocked.status_code == 429, blocked.text

    missing = await client.post("/submissions/999/evaluate", headers=owner_auth)
    assert missing.status_code == 404, missing.text

    queued = await client.post(f"/submissions/{submission_id}/evaluate", headers=owner_auth)
    assert queued.status_code == 201, queued.text
    run = queued.json()
    assert run["status"] == "queued"
    assert run["rules_version"] == "rules-v1.1"
    assert [seed["seed_label"] for seed in run["seed_runs"]] == [
        "hidden-1",
        "hidden-2",
        "hidden-3",
        "hidden-4",
        "hidden-5",
    ]
    assert all(seed["status"] == "queued" and seed["attempt"] == 1 for seed in run["seed_runs"])
    assert await db_session.scalar(select(func.count(EvaluationSeedRun.id))) == 5
    assert await db_session.scalar(
        select(func.count(AuditEvent.id)).where(AuditEvent.action == "evaluation.enqueued")
    ) == 1

    active = await client.post(f"/submissions/{submission_id}/evaluate", headers=owner_auth)
    assert active.status_code == 409, active.text

    other_auth = await _login(client, "competition-other@hku.hk")
    denied = await client.get(f"/submissions/{submission_id}", headers=other_auth)
    assert denied.status_code == 403, denied.text
    other_user = (
        await db_session.execute(select(User).where(User.email == "competition-other@hku.hk"))
    ).scalar_one()
    other_user.role = "admin"
    await db_session.commit()
    admin_detail = await client.get(f"/submissions/{submission_id}", headers=other_auth)
    assert admin_detail.status_code == 200, admin_detail.text
    detail = await client.get(f"/submissions/{submission_id}", headers=owner_auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["latest_run"]["id"] == run["id"]
    assert "seed_value" not in detail.text


@pytest.mark.asyncio
async def test_competition_practice_seeds_are_deterministic_and_secret_seed_values_do_not_leak(client):
    from eflux.evaluation.seeds import seed_values

    first = await client.get("/competitions/season-0")
    second = await client.get("/competitions/season-0")
    assert first.status_code == 200, first.text
    assert first.json()["practice_seed_values"] == second.json()["practice_seed_values"]
    assert first.json()["practice_seed_values"] == seed_values("season-0", "practice", 3)
    assert first.json()["hidden_seed_count"] == 5
    assert first.json()["holdout_seed_count"] == 2
    for value in [*seed_values("season-0", "hidden", 5), *seed_values("season-0", "holdout", 2)]:
        assert str(value) not in first.text


@pytest.mark.asyncio
async def test_competition_leaderboard_ranks_scored_submissions_masks_email_and_excludes_runs(
    client, db_session
):
    from eflux.db.models import (
        Competition,
        EvaluationRun,
        EvaluationSeedRun,
        Submission,
        User,
    )

    competition = (await db_session.execute(select(Competition))).scalar_one()
    users = [
        User(email="nikoneo@gmail.com"),
        User(email="albert@example.com"),
        User(email="hidden@example.com"),
    ]
    db_session.add_all(users)
    await db_session.flush()
    submissions = [
        Submission(
            competition_id=competition.id,
            user_id=user.id,
            track="managed",
            status="finalized",
            payload={"algorithm": algorithm, "llm_enabled": False, "preset": "Solar Trader"},
        )
        for user, algorithm in zip(users, ["ppo", "zip", "aa"], strict=True)
    ]
    db_session.add_all(submissions)
    await db_session.flush()
    runs = [
        EvaluationRun(submission_id=submission.id, status="completed", rules_version="rules-v1.1", score=score, summary=summary)
        for submission, score, summary in zip(
            submissions,
            [4.0, 9.0, 99.0],
            [{}, {}, {"excluded": True}],
            strict=True,
        )
    ]
    db_session.add_all(runs)
    await db_session.flush()
    db_session.add_all(
        [
            EvaluationSeedRun(evaluation_run_id=runs[0].id, seed_label="hidden-1", status="completed"),
            EvaluationSeedRun(evaluation_run_id=runs[0].id, seed_label="hidden-2", status="failed"),
            EvaluationSeedRun(evaluation_run_id=runs[1].id, seed_label="hidden-1", status="succeeded"),
            EvaluationSeedRun(evaluation_run_id=runs[2].id, seed_label="hidden-1", status="completed"),
        ]
    )
    await db_session.commit()

    response = await client.get("/competitions/season-0/leaderboard")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "competition_slug": "season-0",
        "entries": [
            {
                "rank": 1,
                "submission_id": submissions[1].id,
                "user_email": "al***@example.com",
                "algorithm": "zip",
                "score": 9.0,
                "seed_ok_count": 1,
                "seed_failed_count": 0,
            },
            {
                "rank": 2,
                "submission_id": submissions[0].id,
                "user_email": "ni***@gmail.com",
                "algorithm": "ppo",
                "score": 4.0,
                "seed_ok_count": 1,
                "seed_failed_count": 1,
            },
        ],
    }


@pytest.mark.asyncio
async def test_final_selection_closes_into_immutable_holdout_run(client, db_session):
    from eflux.db.models import Competition, EvaluationRun, EvaluationSeedRun, Submission, User

    owner_auth = await _login(client, "final-owner@example.com")
    created = await client.post(
        "/competitions/season-0/submissions",
        headers=owner_auth,
        json=_submission_body(algorithm="truthful"),
    )
    submission_id = created.json()["id"]
    submission = await db_session.get(Submission, submission_id)
    hidden = EvaluationRun(
        submission_id=submission_id,
        kind="hidden",
        status="scored",
        rules_version="rules-v1.1",
        score=1.25,
        summary={},
    )
    db_session.add(hidden)
    await db_session.commit()

    selected = await client.post(
        f"/submissions/{submission_id}/select-final", headers=owner_auth
    )
    assert selected.status_code == 200, selected.text

    admin_auth = await _login(client, "final-admin@example.com")
    admin = (
        await db_session.execute(select(User).where(User.email == "final-admin@example.com"))
    ).scalar_one()
    admin.role = "admin"
    await db_session.commit()
    closed = await client.post("/competitions/season-0/close", headers=admin_auth)
    assert closed.status_code == 200, closed.text
    (holdout_run_id,) = closed.json()["holdout_run_ids"]

    holdout = await db_session.get(EvaluationRun, holdout_run_id)
    competition = (await db_session.execute(select(Competition))).scalar_one()
    seed_rows = (
        await db_session.execute(
            select(EvaluationSeedRun)
            .where(EvaluationSeedRun.evaluation_run_id == holdout_run_id)
            .order_by(EvaluationSeedRun.id)
        )
    ).scalars().all()
    await db_session.refresh(submission)
    assert competition.status == "closed"
    assert competition.closed_at is not None
    assert submission is not None and submission.selected_for_final is True
    assert holdout is not None and holdout.kind == "holdout"
    assert holdout.manifest["parameters"]["submission_payload"] == submission.payload
    assert [seed.seed_label for seed in seed_rows] == ["holdout-1", "holdout-2"]
