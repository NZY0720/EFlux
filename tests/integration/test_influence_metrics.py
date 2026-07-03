"""LLM/PPO influence telemetry on the public agents roster."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_agents_roster_includes_influence_metrics_with_non_hybrid_defaults(client):
    r = await client.get("/market/agents")
    assert r.status_code == 200, r.text

    agent = next(a for a in r.json() if not a["is_llm"] and a["mirror_of"] is None)
    assert agent["fallback_count"] >= 0
    assert agent["veto_hold_count"] >= 0
    assert agent["risk_rejections"] >= 0
    assert agent["decide_ticks"] >= 0
    assert agent["guidance_change_rate"] is None
    assert agent["mode_override_rate"] is None
    assert agent["avg_price_dev_bps"] is None
