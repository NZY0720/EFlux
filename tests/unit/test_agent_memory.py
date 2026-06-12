"""Unit tests for the per-agent JSONL learning memory."""

from __future__ import annotations

import json

from eflux.agents.reflective.memory import AgentMemory, slug


def _record(tick: int, pnl: float) -> dict:
    return {
        "v": 1,
        "ts": "2026-06-12T08:30:05+00:00",
        "sim_ts": "2026-06-12T16:30:05+08:00",
        "tick": tick,
        "hints": {"price_adjust": 0.05, "qty_scale": 1.2},
        "rationale": "bid up into evening peak",
        "lesson": "evening deficits clear faster bidding ~5% over ref",
        "window": {"ticks": 60, "pnl": pnl, "trades": 3, "soc_end": 0.41},
    }


def test_append_load_round_trip(tmp_path):
    path = tmp_path / "agent.jsonl"
    mem = AgentMemory(path)
    mem.append(_record(60, 1.84))
    mem.append(_record(120, -0.3))

    fresh = AgentMemory(path)
    assert fresh.load() == 2
    assert [r["tick"] for r in fresh.records] == [60, 120]
    assert fresh.last(1)[0]["window"]["pnl"] == -0.3


def test_survives_restart_on_same_path(tmp_path):
    """The whole point: lessons persist across backend restarts."""
    path = tmp_path / "agent.jsonl"
    AgentMemory(path).append(_record(60, 1.0))

    reborn = AgentMemory(path)
    reborn.load()
    reborn.append(_record(120, 2.0))

    assert len(path.read_text().splitlines()) == 2
    third = AgentMemory(path)
    assert third.load() == 2


def test_corrupt_lines_skipped(tmp_path):
    path = tmp_path / "agent.jsonl"
    path.write_text(
        json.dumps(_record(60, 1.0))
        + "\n{torn write no closing brace\n\n"
        + json.dumps(_record(120, 2.0))
        + "\n",
        encoding="utf-8",
    )
    mem = AgentMemory(path)
    assert mem.load() == 2
    assert [r["tick"] for r in mem.records] == [60, 120]


def test_load_caps_at_max_loaded(tmp_path):
    path = tmp_path / "agent.jsonl"
    mem = AgentMemory(path, max_loaded=3)
    for i in range(10):
        mem.append(_record(i, float(i)))

    fresh = AgentMemory(path, max_loaded=3)
    assert fresh.load() == 3
    assert [r["tick"] for r in fresh.records] == [7, 8, 9]  # the tail


def test_missing_file_is_fresh_start(tmp_path):
    mem = AgentMemory(tmp_path / "never-written.jsonl")
    assert mem.load() == 0
    assert mem.last(5) == []


def test_in_memory_mode_without_path():
    mem = AgentMemory(None)
    mem.append(_record(60, 1.0))
    assert mem.load() == 0  # nothing on disk
    assert len(mem.records) == 1  # but the buffer works


def test_slug_is_filesystem_safe():
    assert slug("my-llm-vpp") == "my-llm-vpp"
    assert slug("LLM Agent #2 (beta)") == "llm-agent-2-beta"
    assert slug("???") == "agent"
