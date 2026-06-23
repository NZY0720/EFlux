"""Structured LLM guidance tests (M6): parsing, soft application, and the async refresh."""

from __future__ import annotations

import pytest

from eflux.agents.reflective.strategist import (
    GuidanceParseError,
    LLMStrategist,
    StrategyGuidance,
    apply_guidance,
    parse_guidance,
)
from eflux.agents.strategy.schema import StrategyAction, StrategyMode


def test_parse_guidance_extracts_and_clamps():
    raw = """Here is my advice:
    {"preferred_modes": ["ladder_sell", "passive_market_make"], "avoid_modes": ["aggressive_taker"],
     "risk_budget": 0.4, "soc_target": 0.55, "execution_style": "Prefer maker orders.",
     "lesson": "Crossed the spread too often last window."}
    """
    g = parse_guidance(raw)
    assert g.preferred_modes == (StrategyMode.LADDER_SELL, StrategyMode.PASSIVE_MARKET_MAKE)
    assert g.avoid_modes == (StrategyMode.AGGRESSIVE_TAKER,)
    assert g.risk_budget == 0.4 and g.soc_target == 0.55
    assert "maker" in g.execution_style


def test_parse_guidance_clamps_out_of_range_and_drops_unknown_modes():
    g = parse_guidance('{"preferred_modes": ["bogus", "noop"], "risk_budget": 5.0, "soc_target": -1.0}')
    assert g.preferred_modes == (StrategyMode.NOOP,)  # unknown silently dropped
    assert g.risk_budget == 1.0 and g.soc_target == 0.0  # clamped to [0,1]


def test_parse_guidance_handles_code_fences():
    g = parse_guidance('```json\n{"risk_budget": 0.7}\n```')
    assert g.risk_budget == 0.7


def test_parse_guidance_raises_on_garbage():
    with pytest.raises(GuidanceParseError):
        parse_guidance("no json here at all")


def test_apply_guidance_scales_size_by_risk_budget():
    a = StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, qty_fraction=1.0, aggressiveness=0.8)
    out = apply_guidance(a, StrategyGuidance(risk_budget=0.5, soc_target=0.6))
    assert out.qty_fraction == 0.5 and out.aggressiveness == pytest.approx(0.4)
    assert out.soc_target == 0.6
    assert out.mode is StrategyMode.LIQUIDATE_SURPLUS  # primitive untouched


def test_apply_guidance_discourages_avoided_mode_softly():
    a = StrategyAction(mode=StrategyMode.AGGRESSIVE_TAKER, qty_fraction=1.0)
    out = apply_guidance(a, StrategyGuidance(avoid_modes=(StrategyMode.AGGRESSIVE_TAKER,), risk_budget=1.0))
    # Shrunk, not vetoed — still the same primitive (soft prior, principle #4).
    assert out.qty_fraction == 0.25 and out.mode is StrategyMode.AGGRESSIVE_TAKER


def test_apply_guidance_none_is_identity():
    a = StrategyAction(mode=StrategyMode.COVER_DEFICIT, qty_fraction=0.7)
    assert apply_guidance(a, None) == a


@pytest.mark.asyncio
async def test_llm_strategist_refresh_parses_and_caches():
    class FakeClient:
        async def chat(self, messages, *, temperature=0.2):
            return '{"risk_budget": 0.3, "soc_target": 0.45, "avoid_modes": ["aggressive_taker"]}'

    s = LLMStrategist(client=FakeClient())
    assert s.current_guidance() is None
    g = await s.arefresh(recent_pnl=[1.0, -0.5], soc_frac=0.5, best_bid=49.0, best_ask=51.0, last_price=50.0)
    assert g.risk_budget == 0.3
    assert s.current_guidance() is g


@pytest.mark.asyncio
async def test_llm_strategist_keeps_prior_guidance_on_failure():
    class FlakyClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, *, temperature=0.2):
            self.calls += 1
            if self.calls == 1:
                return '{"risk_budget": 0.6}'
            raise RuntimeError("endpoint down")

    s = LLMStrategist(client=FlakyClient())
    await s.arefresh(recent_pnl=[], soc_frac=0.5, best_bid=None, best_ask=None, last_price=None)
    assert s.current_guidance().risk_budget == 0.6
    # Second call fails → prior guidance is retained, not blanked.
    await s.arefresh(recent_pnl=[], soc_frac=0.5, best_bid=None, best_ask=None, last_price=None)
    assert s.current_guidance().risk_budget == 0.6
