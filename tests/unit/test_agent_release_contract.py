from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from eflux.agents.ppo.bc import BCNet, save_bc
from eflux.ecosystem import service


def _release(**overrides):
    values = {
        "market": "p2p",
        "recipe": {
            "algorithm": "truthful",
            "agent_params": {},
            "protocol_version": "1",
            "observation_schema_version": "1",
            "action_schema_version": "1",
            "online_learning": False,
            "fallback_strategy": "safe_hold",
            "risk_limits": {
                "max_open_orders": 256,
                "max_new_orders_per_decision": 20,
                "credit_limit_usd": 1000,
            },
            "order_routing": {"markets": ["p2p"], "default_route": "auto"},
        },
        "state": {},
        "compatibility": {"market": "p2p", "profile_id": "battery-only"},
        "environment": {
            "runtime": "eflux-managed",
            "agent_protocol_version": 1,
            "dependencies_locked": True,
            "git_commit": "abcdef0",
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_release_contract_rejects_empty_and_algorithm_inconsistent_recipe():
    with pytest.raises(service.EcosystemError, match="recipe must be a non-empty"):
        service.validate_agent_release_for_publish(_release(recipe={}))

    recipe = dict(_release().recipe)
    recipe["online_learning"] = True
    with pytest.raises(service.EcosystemError, match=r"only for recipe\.algorithm='ppo'"):
        service.validate_agent_release_for_publish(_release(recipe=recipe))


def test_llm_release_requires_complete_reproducible_configuration():
    recipe = dict(_release().recipe)
    recipe["algorithm"] = "scripted"
    recipe["llm"] = {"provider": "openai-compatible", "model": "example"}
    with pytest.raises(service.EcosystemError, match="system_prompt"):
        service.validate_agent_release_for_publish(_release(recipe=recipe))

    recipe["llm"] = {
        "provider": "openai-compatible",
        "model": "example-model",
        "system_prompt": "Act as a cautious energy trader.",
        "prompt_template": "strategist-v1",
        "credential_env": "${EFLUX_RELEASE_LLM_KEY}",
        "temperature": 0.2,
        "max_tokens": 1024,
        "guidance_refresh_interval_seconds": 300,
        "memory": {"window_messages": 12},
        "timeout_seconds": 30,
        "fallback": "cached_guidance_then_hold",
        "cost_estimate": {
            "input_usd_per_million_tokens": 3,
            "output_usd_per_million_tokens": 15,
        },
    }
    recipe["algorithm"] = "truthful"
    with pytest.raises(service.EcosystemError, match="supported only for scripted"):
        service.validate_agent_release_for_publish(_release(recipe=recipe))
    recipe["algorithm"] = "scripted"
    service.validate_agent_release_for_publish(_release(recipe=recipe))


def test_deterministic_llm_replay_archives_are_valid_release_state():
    service.validate_agent_release_for_publish(
        _release(
            state={
                "llm_replay_archives": [
                    {
                        "messages": [{"role": "system", "content": "archived"}],
                        "response": "{}",
                        "model": "example-model",
                    }
                ]
            }
        )
    )


def test_ppo_release_checkpoint_is_contained_hashed_loadable_and_schema_bound(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    checkpoint_dir = project / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    checkpoint = checkpoint_dir / "agent.pt"
    network = BCNet()
    save_bc(network, str(checkpoint))
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    monkeypatch.setattr(service, "PROJECT_ROOT", project)

    recipe = dict(_release().recipe)
    recipe.update(
        algorithm="ppo",
        observation_schema_version=str(network.obs_version),
        action_schema_version=str(network.encoding_version),
    )
    state = {"checkpoint_path": "checkpoints/agent.pt", "checkpoint_sha256": digest}
    service.validate_agent_release_for_publish(_release(recipe=recipe, state=state))

    mismatched = dict(state, checkpoint_sha256="0" * 64)
    with pytest.raises(service.EcosystemError, match="does not match"):
        service.validate_agent_release_for_publish(_release(recipe=recipe, state=mismatched))

    recipe["action_schema_version"] = "999"
    with pytest.raises(service.EcosystemError, match="action schema"):
        service.validate_agent_release_for_publish(_release(recipe=recipe, state=state))
