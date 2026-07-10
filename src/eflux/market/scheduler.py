"""Snapshot-consistent decision rounds with deterministic arrival fairness."""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from eflux.agents.decision import AgentDecision

ContextT = TypeVar("ContextT")


@dataclass(frozen=True, slots=True)
class ScheduledDecision:
    participant_id: int
    decision: AgentDecision


@dataclass(frozen=True, slots=True)
class DecisionRound:
    cycle_id: int
    sim_ts: datetime
    snapshot_id: str
    decisions: tuple[ScheduledDecision, ...]


class FairDecisionScheduler:
    """Collect first, then execute in a seeded rotating participant order.

    The base order is deterministically shuffled for each roster. Rotating it by
    cycle gives every stable participant each arrival position exactly once per
    roster-length cycles, avoiding permanent first-mover advantage.
    """

    def __init__(
        self,
        *,
        epoch: datetime,
        cadence_sec: float = 30.0,
        seed: int = 0,
    ) -> None:
        if epoch.tzinfo is None or epoch.utcoffset() is None:
            raise ValueError("epoch must be timezone-aware")
        if not math.isfinite(cadence_sec) or cadence_sec <= 0.0:
            raise ValueError("cadence_sec must be finite and positive")
        self.epoch = epoch.astimezone(UTC)
        self.cadence_sec = cadence_sec
        self.seed = seed
        self._last_collected_cycle = -1

    def cycle_id(self, sim_ts: datetime) -> int:
        if sim_ts.tzinfo is None or sim_ts.utcoffset() is None:
            raise ValueError("sim_ts must be timezone-aware")
        elapsed = (sim_ts.astimezone(UTC) - self.epoch).total_seconds()
        return math.floor(elapsed / self.cadence_sec)

    def is_due(self, sim_ts: datetime) -> bool:
        cycle = self.cycle_id(sim_ts)
        return cycle >= 0 and cycle > self._last_collected_cycle

    def arrival_order(self, participant_ids: Iterable[int], cycle_id: int) -> tuple[int, ...]:
        roster = tuple(sorted(set(participant_ids)))
        if not roster:
            return ()
        material = f"{self.seed}:" + ",".join(str(pid) for pid in roster)
        digest = hashlib.sha256(material.encode()).digest()
        base = list(roster)
        random.Random(int.from_bytes(digest[:8], "big")).shuffle(base)
        offset = cycle_id % len(base)
        return tuple(base[offset:] + base[:offset])

    def collect(
        self,
        *,
        sim_ts: datetime,
        participant_ids: Iterable[int],
        build_context: Callable[[int], ContextT],
        decide: Callable[[int, ContextT], AgentDecision],
        snapshot_id: str,
    ) -> DecisionRound:
        cycle = self.cycle_id(sim_ts)
        if cycle < 0:
            raise ValueError("cannot collect a decision round before the scheduler epoch")
        if cycle <= self._last_collected_cycle:
            raise ValueError(f"decision cycle {cycle} was already collected")
        roster = tuple(sorted(set(participant_ids)))

        # Phase 1: freeze every participant's context before any decision can be
        # submitted to the venue. Context construction must not mutate the book.
        contexts = {pid: build_context(pid) for pid in roster}
        # Phase 2a: collect decisions. Their execution order is determined only
        # after all participants have seen the same market snapshot.
        by_participant = {pid: decide(pid, contexts[pid]) for pid in roster}
        ordered = tuple(
            ScheduledDecision(pid, by_participant[pid]) for pid in self.arrival_order(roster, cycle)
        )
        self._last_collected_cycle = cycle
        return DecisionRound(
            cycle_id=cycle,
            sim_ts=sim_ts.astimezone(UTC),
            snapshot_id=snapshot_id,
            decisions=ordered,
        )
