"""Unit tests for ExternalStrategist + external guidance coercion (Tier A3)."""

from __future__ import annotations

from collections import deque

from eflux.agents.reflective.strategist import (
    ExternalStrategist,
    LLMStrategist,
    StrategyGuidance,
    external_guidance_from_dict,
    modes_from_names,
)
from eflux.agents.strategy.schema import StrategyMode

# The reflections read path (ReflectionEntryOut, /market/reflections) parses these keys.
ENTRY_KEYS = {
    "ts",
    "ok",
    "preferred_modes",
    "avoid_modes",
    "risk_budget",
    "soc_target",
    "execution_style",
    "rationale",
    "lesson",
    "meta_control",
    "error",
}


def test_modes_from_names_drops_unknown_softly():
    modes = modes_from_names(["ladder_sell", "BOGUS", " Battery_Arbitrage "])
    assert modes == (StrategyMode.LADDER_SELL, StrategyMode.BATTERY_ARBITRAGE)


def test_external_guidance_from_dict_clamps_and_sanitizes():
    g, meta = external_guidance_from_dict(
        {
            "preferred_modes": ["liquidate_surplus"],
            "risk_budget": 99.0,
            "soc_target": -3.0,
            "execution_style": "x" * 500,
            "meta_control": {"lr": 100.0, "unknown_key": 1.0},
        }
    )
    assert g.risk_budget == 1.0
    assert g.soc_target == 0.0
    assert len(g.execution_style) <= 200
    assert meta is not None
    assert meta.lr <= 1e-3  # hard-clamped to the MetaControl range
    # realprice sanitization drops disallowed preferred modes but never errors.
    g_rp, _ = external_guidance_from_dict(
        {"preferred_modes": ["liquidate_surplus"]}, market_mode="realprice"
    )
    assert isinstance(g_rp, StrategyGuidance)


def test_entry_shape_matches_llm_strategist():
    ext = ExternalStrategist()
    g, meta = external_guidance_from_dict({"risk_budget": 0.5, "lesson": "test"})
    entry = ext.set_guidance(g, meta)
    assert set(entry.keys()) == ENTRY_KEYS
    assert entry["ok"] is True
    assert entry["risk_budget"] == 0.5
    assert ext.current_guidance() is g
    assert ext.current_meta() is meta
    assert ext.ok_count == 1
    assert ext.last_ok_ts is not None
    assert list(ext.reflection_log)[-1] is entry


def test_no_arefresh_attribute_means_no_platform_llm_calls():
    """HybridPolicyAgent._maybe_refresh_guidance keys off getattr(strategist, "arefresh"):
    its absence is the contract that keeps the platform LLM idle under external steering."""
    assert not hasattr(ExternalStrategist(), "arefresh")


def test_prior_and_log_continuity():
    prior = LLMStrategist(client=object())
    prior.reflection_log.append({"ok": True, "marker": "from-llm"})
    ext = ExternalStrategist(
        prior=prior, client=prior.client, reflection_log=prior.reflection_log
    )
    g, _ = external_guidance_from_dict({})
    ext.set_guidance(g)
    # Timeline is continuous (platform entry still there) and prior is restorable.
    assert next(iter(ext.reflection_log))["marker"] == "from-llm"
    assert len(ext.reflection_log) == 2
    assert ext.prior is prior
    assert ext.client is prior.client


def test_fresh_log_when_no_prior():
    ext = ExternalStrategist(reflection_log=deque(maxlen=50))
    assert ext.prior is None
    assert ext.current_guidance() is None
