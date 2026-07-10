from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eflux.agents.decision import AgentDecision
from eflux.market.scheduler import FairDecisionScheduler

EPOCH = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_decision_cadence_is_independent_of_one_second_physics_ticks():
    scheduler = FairDecisionScheduler(epoch=EPOCH, cadence_sec=30)
    assert scheduler.is_due(EPOCH)
    scheduler.collect(
        sim_ts=EPOCH,
        participant_ids=(),
        build_context=lambda _pid: None,
        decide=lambda _pid, _ctx: AgentDecision.hold(),
        snapshot_id="0",
    )
    assert not scheduler.is_due(EPOCH + timedelta(seconds=29))
    assert scheduler.is_due(EPOCH + timedelta(seconds=30))


def test_two_phase_collection_builds_every_context_before_any_decision():
    scheduler = FairDecisionScheduler(epoch=EPOCH)
    calls: list[str] = []

    def build(pid: int) -> int:
        calls.append(f"observe:{pid}")
        return pid

    def decide(pid: int, ctx: int) -> AgentDecision:
        calls.append(f"decide:{pid}:{ctx}")
        return AgentDecision.hold()

    scheduler.collect(
        sim_ts=EPOCH,
        participant_ids=(3, 1, 2),
        build_context=build,
        decide=decide,
        snapshot_id="book-v1",
    )
    first_decide = next(i for i, call in enumerate(calls) if call.startswith("decide:"))
    assert calls[:first_decide] == ["observe:1", "observe:2", "observe:3"]


def test_rotating_seeded_order_is_reproducible_and_position_fair():
    a = FairDecisionScheduler(epoch=EPOCH, seed=42)
    b = FairDecisionScheduler(epoch=EPOCH, seed=42)
    roster = (10, 20, 30, 40)
    orders_a = [a.arrival_order(roster, cycle) for cycle in range(len(roster))]
    orders_b = [b.arrival_order(reversed(roster), cycle) for cycle in range(len(roster))]
    assert orders_a == orders_b
    for participant in roster:
        assert sorted(order.index(participant) for order in orders_a) == list(range(len(roster)))


def test_cycle_cannot_be_collected_twice():
    scheduler = FairDecisionScheduler(epoch=EPOCH)
    kwargs = dict(
        sim_ts=EPOCH,
        participant_ids=(),
        build_context=lambda _pid: None,
        decide=lambda _pid, _ctx: AgentDecision.hold(),
        snapshot_id="0",
    )
    scheduler.collect(**kwargs)
    with pytest.raises(ValueError, match="already collected"):
        scheduler.collect(**kwargs)
