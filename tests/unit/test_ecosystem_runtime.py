from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from eflux.agents.aa_agent import AAAgent
from eflux.agents.gd_agent import GDAgent
from eflux.agents.hybrid import StrategyAgent
from eflux.agents.strategy.policy import ScriptedStrategyPolicy
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.zip_agent import ZIPAgent
from eflux.ecosystem import runtime
from eflux.ecosystem.catalog import list_builtin_population_packs
from eflux.ecosystem.runtime import (
    AdversarialPressureAgent,
    ArchivedGuidanceHybridAgent,
    SeededZeroIntelligenceAgent,
    agent_factory_from_release,
    bench_roster_from_population,
)


@pytest.mark.parametrize(
    ("algorithm", "expected"),
    [
        ("truthful", TruthfulAgent),
        ("zip", ZIPAgent),
        ("gd", GDAgent),
        ("aa", AAAgent),
        ("scripted", StrategyAgent),
        ("strategy", StrategyAgent),
    ],
)
def test_agent_factory_uses_only_shipped_allowlisted_agents(algorithm, expected):
    release = SimpleNamespace(
        recipe={"algorithm": algorithm, "agent_params": {"price_ref": "47.5"}},
        state={},
    )

    agent = agent_factory_from_release(release)

    assert isinstance(agent, expected)
    assert agent.price_ref == Decimal("47.5")


def test_agent_factory_rejects_unknown_params_and_runtime_capabilities():
    with pytest.raises(ValueError, match="not accepted"):
        agent_factory_from_release(
            {"recipe": {"algorithm": "truthful", "agent_params": {"model": "x"}}}
        )
    with pytest.raises(ValueError, match="endpoint"):
        agent_factory_from_release(
            {
                "recipe": {
                    "algorithm": "truthful",
                    "agent_params": {},
                    "endpoint": "https://untrusted.invalid",
                }
            }
        )
    with pytest.raises(ValueError, match="command"):
        agent_factory_from_release(
            {"recipe": {"algorithm": "truthful"}, "state": {"command": ["sh"]}}
        )


def _patch_checkpoint_roots(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    checkpoints = project / "checkpoints"
    training_runs = project / "artifacts" / "training_runs"
    checkpoints.mkdir(parents=True)
    training_runs.mkdir(parents=True)
    monkeypatch.setattr(runtime, "PROJECT_ROOT", project)
    monkeypatch.setattr(runtime, "_CHECKPOINT_ROOTS", (checkpoints, training_runs))
    return checkpoints, training_runs


def test_ppo_release_requires_contained_hash_bound_checkpoint(monkeypatch, tmp_path):
    checkpoints, _training_runs = _patch_checkpoint_roots(monkeypatch, tmp_path)
    checkpoint = checkpoints / "release.pt"
    checkpoint.write_bytes(b"platform checkpoint")
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    captured = {}

    def fake_policy(path, *, learning, seed):
        captured.update(path=path, learning=learning, seed=seed)
        return ScriptedStrategyPolicy()

    monkeypatch.setattr(runtime, "_ppo_policy", fake_policy)
    release = {
        "recipe": {"algorithm": "ppo", "seed": 19, "agent_params": {"min_qty": "0.02"}},
        "state": {"checkpoint_path": "checkpoints/release.pt", "checkpoint_sha256": digest},
    }

    agent = agent_factory_from_release(release, learning=True)

    assert isinstance(agent, StrategyAgent)
    assert agent.min_qty == Decimal("0.02")
    assert captured == {"path": checkpoint.resolve(), "learning": True, "seed": 19}


def test_ppo_release_rejects_hash_mismatch_and_path_escape(monkeypatch, tmp_path):
    checkpoints, _training_runs = _patch_checkpoint_roots(monkeypatch, tmp_path)
    inside = checkpoints / "inside.pt"
    inside.write_bytes(b"inside")
    wrong_digest = hashlib.sha256(b"different").hexdigest()
    with pytest.raises(ValueError, match="does not match"):
        agent_factory_from_release(
            {
                "recipe": {"algorithm": "ppo"},
                "state": {
                    "checkpoint_path": str(inside),
                    "checkpoint_sha256": wrong_digest,
                },
            }
        )

    outside = tmp_path / "outside.pt"
    outside.write_bytes(b"outside")
    outside_digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="must be under"):
        agent_factory_from_release(
            {
                "recipe": {"algorithm": "ppo"},
                "state": {
                    "checkpoint_path": str(outside),
                    "checkpoint_sha256": outside_digest,
                },
            }
        )

    symlink = checkpoints / "escaped.pt"
    symlink.symlink_to(outside)
    with pytest.raises(ValueError, match="must be under"):
        agent_factory_from_release(
            {
                "recipe": {"algorithm": "ppo"},
                "state": {
                    "checkpoint_path": str(symlink),
                    "checkpoint_sha256": outside_digest,
                },
            }
        )


def test_population_roster_count_profiles_and_seed_are_reproducible():
    pack = next(item for item in list_builtin_population_packs() if item["id"] == "low-liquidity")

    first = bench_roster_from_population(pack, seed=917)
    second = bench_roster_from_population(pack, seed=917)
    different = bench_roster_from_population(pack, seed=918)

    assert len(first) == 8

    def fingerprint(roster):
        return [
            (row.name, row.params.to_dict(), type(row.agent).__name__, row.seed) for row in roster
        ]

    assert fingerprint(first) == fingerprint(second)
    assert fingerprint(first) != fingerprint(different)
    assert all(isinstance(row.params.battery_kwh, float) for row in first)


def test_population_factory_supports_every_catalog_strategy_without_live_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "_platform_p2p_checkpoint", lambda: tmp_path / "known.pt")
    monkeypatch.setattr(
        runtime,
        "_ppo_policy",
        lambda _path, *, learning, seed: ScriptedStrategyPolicy(),
    )
    strategies = [
        "truthful",
        "zip",
        "gd",
        "aa",
        "zero_intelligence",
        "ppo",
        "llm_hybrid",
        "adversarial",
    ]
    pack = {
        "id": "all-strategies",
        "spec": {
            "roster": [
                {"strategy": strategy, "count": 1, "profile_pool": ["battery-only"]}
                for strategy in strategies
            ],
            "scenario": {},
        },
    }

    roster = bench_roster_from_population(pack, seed=7)

    assert [type(row.agent) for row in roster] == [
        TruthfulAgent,
        ZIPAgent,
        GDAgent,
        AAAgent,
        SeededZeroIntelligenceAgent,
        StrategyAgent,
        ArchivedGuidanceHybridAgent,
        AdversarialPressureAgent,
    ]
    offline_hybrid = roster[6].agent
    assert isinstance(offline_hybrid, ArchivedGuidanceHybridAgent)
    assert offline_hybrid.guidance_source == "platform_archived_static"
    assert not hasattr(offline_hybrid.strategist, "arefresh")
    assert offline_hybrid.strategist.current_guidance() is not None
