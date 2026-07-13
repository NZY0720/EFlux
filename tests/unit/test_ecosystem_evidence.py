from __future__ import annotations

from eflux.ecosystem.evaluation import (
    _hidden_population_packs,
    _population_evidence,
)
from eflux.ecosystem.runtime import bench_roster_from_population


def test_worker_hidden_rosters_are_runnable_but_evidence_discloses_only_fingerprint() -> None:
    packs = _hidden_population_packs()

    assert len(packs) >= 2
    for pack in packs:
        roster = bench_roster_from_population(pack, seed=123)
        public = _population_evidence(pack)
        assert roster
        assert public["worker_hidden"] is True
        assert public["spec"]["roster_fingerprint"] == pack["content_sha256"]
        assert "roster" not in public["spec"]
