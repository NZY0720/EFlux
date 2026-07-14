from __future__ import annotations

from datetime import UTC, datetime

import pytest

from eflux.agents.bench.run import run_episode
from eflux.agents.ppo import training_data
from eflux.ecosystem.catalog import get_standard_profile
from eflux.ecosystem.evaluation import _historical_grid_protocol, _protocol_replay
from eflux.ecosystem.llm_replay import build_historical_llm_agent
from eflux.vpp.base import VPPParams


class FakeMeteredClient:
    model = "fake-current-model"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def usage(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.calls * 10,
            "completion_tokens": self.calls * 5,
            "estimated_cost_usd": self.calls * 0.001,
        }

    async def chat(self, messages, *, temperature=0.2, max_tokens=4096):
        del messages, temperature, max_tokens
        self.calls += 1
        return (
            '{"risk_budget": 0.8, "passive_only": false, '
            '"execution_style": "archived test guidance"}'
        )

    async def aclose(self) -> None:
        return None


def _release() -> dict:
    return {
        "id": 1,
        "name": "llm-release",
        "version": "1",
        "market": "realprice",
        "recipe": {
            "algorithm": "scripted",
            "fallback_strategy": "safe_hold",
            "llm": {
                "system_prompt": "Return strict JSON guidance.",
                "prompt_template": "Use only the historical state below.",
                "prompt_template_version": "test-v1",
                "temperature": 0.2,
                "max_tokens": 256,
                "timeout_seconds": 10,
                "guidance_refresh_every_n_ticks": 6,
            },
        },
        "state": {},
        "compatibility": {},
        "environment": {},
        "content_sha256": "a" * 64,
    }


def _battery_params() -> VPPParams:
    profile = get_standard_profile("battery-only")
    return VPPParams.from_dict(profile["spec"]["vpp_params"])


def test_archived_llm_replay_hash_checks_prompts_and_reproduces_episode() -> None:
    release = _release()
    client = FakeMeteredClient()
    agent = build_historical_llm_agent(release, client=client)
    _sim, vpp = run_episode(
        lambda: agent,
        n_ticks=12,
        tick_h=5.0 / 60.0,
        episode_seed=11,
        market_mode="realprice",
        candidate_params=_battery_params(),
    )
    archive = list(vpp.agent.strategist.replay_archive)
    vpp.agent.close_historical_llm(require_archive_consumed=False)
    assert len(archive) == 2
    assert all(row["prompt_template_version"] == "test-v1" for row in archive)

    release["state"] = {"llm_replay_archives": {"11": archive}}
    metrics, evidence = _protocol_replay(
        release,
        {"interval_count": 12, "seeds": [11]},
        market="realprice",
        llm_mode="archived",
    )

    assert metrics["llm_call_count_mean"] == 2
    assert evidence["context"]["llm_calls_are_archived_and_hash_checked"] is True
    replayed = evidence["per_seed"][0]["llm_transcript"]
    assert [row["prompt_sha256"] for row in replayed] == [row["prompt_sha256"] for row in archive]


def test_archived_llm_replay_rejects_prompt_drift() -> None:
    release = _release()
    client = FakeMeteredClient()
    agent = build_historical_llm_agent(release, client=client)
    _sim, vpp = run_episode(
        lambda: agent,
        n_ticks=12,
        tick_h=5.0 / 60.0,
        episode_seed=11,
        market_mode="realprice",
        candidate_params=_battery_params(),
    )
    archive = list(vpp.agent.strategist.replay_archive)
    vpp.agent.close_historical_llm(require_archive_consumed=False)
    release["state"] = {"llm_replay_archives": {"11": archive}}
    release["recipe"]["llm"]["prompt_template"] = "A changed prompt."

    with pytest.raises(Exception, match="prompt hash mismatch"):
        _protocol_replay(
            release,
            {"interval_count": 12, "seeds": [11]},
            market="realprice",
            llm_mode="archived",
        )


def test_historical_protocol_binds_platform_price_path_hash(monkeypatch) -> None:
    class FakeHistory:
        def __init__(self) -> None:
            self.price = [40.0, 60.0]

        def price_at(self, ts: datetime) -> float:
            assert ts.tzinfo is UTC
            return 40.0 if ts.minute < 30 else 60.0

    monkeypatch.setattr(
        training_data,
        "load_real_market_data",
        lambda **_kwargs: FakeHistory(),
    )
    prices, count, context = _historical_grid_protocol(
        {"window_start": "2024-01-01", "window_end": "2024-01-02"},
        12,
    )

    assert count == 12
    assert prices is not None and prices[:7] == [40] * 6 + [60]
    assert context["price_provenance"] == "platform_loaded_caiso_history"
    assert len(context["historical_price_sha256"]) == 64
