"""Run the immutable Agent Release -> publish -> evaluation flow locally.

The demo uses the real ecosystem service, ORM models, safe release runtime, and
evaluation worker against an isolated temporary SQLite database. It neither
starts the API server nor writes to the project's development database.

    PYTHONPATH=src .venv/bin/python examples/agent_ecosystem_demo.py
"""

from __future__ import annotations

import asyncio
import json
from tempfile import TemporaryDirectory

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from eflux.db import Base
from eflux.db.models import ReleaseEvaluation, User
from eflux.ecosystem import service
from eflux.ecosystem.runtime_identity import repository_git_commit
from eflux.ecosystem.worker import claim_next_ecosystem_job, execute_ecosystem_job


async def run_demo() -> dict[str, object]:
    """Create, publish, and evaluate one realprice scripted Release."""

    git_commit = repository_git_commit()
    if git_commit is None:
        raise RuntimeError("the demo needs EFLUX_GIT_COMMIT when .git metadata is unavailable")
    with TemporaryDirectory(prefix="eflux-agent-ecosystem-") as directory:
        engine = create_async_engine(f"sqlite+aiosqlite:///{directory}/demo.db")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            async with sessions() as session:
                owner = User(email="ecosystem-demo@example.com")
                session.add(owner)
                await session.flush()

                release = await service.create_agent_release(
                    session,
                    owner,
                    {
                        "name": "Battery Evidence Demo",
                        "version": "1",
                        "description": "A small scripted Release used by the runnable demo.",
                        "market": "realprice",
                        "visibility": "public",
                        "recipe": {
                            "algorithm": "scripted",
                            "agent_params": {"price_ref": "50"},
                            "protocol_version": "1",
                            "observation_schema_version": "1",
                            "action_schema_version": "1",
                            "online_learning": False,
                            "fallback_strategy": "safe_hold",
                            "risk_limits": {
                                "max_open_orders": 20,
                                "max_new_orders_per_decision": 5,
                                "credit_limit_usd": 10_000,
                            },
                            "order_routing": {
                                "markets": ["realprice"],
                                "default_route": "grid",
                            },
                        },
                        "state": {},
                        "compatibility": {
                            "market": "realprice",
                            "profile_ids": ["battery-only"],
                        },
                        "environment": {
                            "runtime": "eflux-managed",
                            "agent_protocol_version": 1,
                            "dependencies_locked": True,
                            "git_commit": git_commit,
                        },
                        "badges": [],
                    },
                )
                await service.publish_agent_release(session, release.id, owner)
                evaluation = await service.create_release_evaluation(
                    session,
                    release.id,
                    owner,
                    {
                        "kind": "deterministic_replay",
                        "config": {
                            "profile_id": "battery-only",
                            "interval_count": 12,
                            "seeds": [11],
                            "grid_price_usd_per_mwh": 50,
                        },
                    },
                )
                release_id = release.id
                evaluation_id = evaluation.id
                await session.commit()

            job = await claim_next_ecosystem_job(sessions)
            if job is None or job.kind != "evaluation" or job.id != evaluation_id:
                raise RuntimeError("the queued Release evaluation was not claimed")
            await execute_ecosystem_job(job, sessions)

            async with sessions() as session:
                release = await service.get_agent_release(session, release_id, None)
                evaluation = await session.get(ReleaseEvaluation, evaluation_id)
                if evaluation is None or evaluation.status != "done":
                    raise RuntimeError("the Release evaluation did not finish")
                return {
                    "release": {
                        "id": release.id,
                        "status": release.status,
                        "content_sha256": release.content_sha256,
                        "badges": release.badges,
                    },
                    "evaluation": {
                        "id": evaluation.id,
                        "status": evaluation.status,
                        "provenance": evaluation.provenance,
                        "evidence_sha256": evaluation.evidence_sha256,
                        "context": (evaluation.evidence or {}).get("context"),
                        "metrics": evaluation.metrics,
                    },
                    "interpretation": (
                        "These are condition-bound evidence dimensions; the platform does not "
                        "combine them into a universal Agent score."
                    ),
                }
        finally:
            await engine.dispose()


def main() -> None:
    print(json.dumps(asyncio.run(run_demo()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
