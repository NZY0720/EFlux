"""Structured LLM guidance tests (M6): parsing, soft application, and the async refresh."""

from __future__ import annotations

import pytest

from eflux.agents.reflective.strategist import (
    GuidanceParseError,
    LLMStrategist,
    MetaControl,
    StrategyGuidance,
    apply_guidance,
    build_strategist_system_prompt,
    build_strategist_user_message,
    parse_guidance,
    parse_meta_control,
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


def test_realprice_prompt_uses_grid_price_taker_context():
    prompt = build_strategist_system_prompt(market_mode="realprice").lower()
    assert "no peer order book" in prompt
    assert "caiso" in prompt and "grid" in prompt
    assert "continuous double auction" not in prompt
    assert "order book depth" not in prompt
    assert "maker" not in prompt


def test_realprice_user_message_carries_grid_fields():
    import json

    msg = build_strategist_user_message(
        recent_pnl=[1.23456],
        soc_frac=0.42,
        best_bid=None,
        best_ask=None,
        last_price=None,
        market_mode="realprice",
        grid_raw_lmp=37.8912,
        grid_import_price=39.8912,
        grid_export_price=35.8912,
        grid_status="real",
    )
    data = json.loads(msg)
    assert data["market_mode"] == "realprice"
    assert data["best_bid"] is None and data["best_ask"] is None
    assert data["grid_raw_lmp"] == 37.8912
    assert data["grid_import_price"] == 39.8912
    assert data["grid_export_price"] == 35.8912
    assert data["grid_status"] == "real"


def test_p2p_user_message_keeps_book_fields_without_grid_fields():
    import json

    msg = build_strategist_user_message(
        recent_pnl=[],
        soc_frac=0.5,
        best_bid=48.0,
        best_ask=52.0,
        last_price=50.0,
        market_mode="p2p",
    )
    data = json.loads(msg)
    assert data["market_mode"] == "p2p"
    assert data["best_bid"] == 48.0 and data["best_ask"] == 52.0
    assert "grid_raw_lmp" not in data


def test_realprice_guidance_drops_book_specific_preferred_modes():
    raw = """{"preferred_modes": ["passive_market_make", "ladder_sell", "cancel_reprice",
      "battery_arbitrage"], "avoid_modes": ["ladder_buy"]}"""
    g = parse_guidance(raw, market_mode="realprice")
    assert g.preferred_modes == (StrategyMode.BATTERY_ARBITRAGE,)
    # Avoid modes are still useful as soft "do not lean this way" explanations.
    assert g.avoid_modes == (StrategyMode.LADDER_BUY,)


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


def test_parse_meta_control_extracts_and_clamps():
    raw = """{"risk_budget": 0.5,
      "meta_control": {"w_soc_mult": 5.0, "w_imbalance_mult": 0.1, "lr": 1e-2,
                       "entropy_coef": 0.2, "kl_target": 0.001, "mode_reg_coef": 2.0}}"""
    m = parse_meta_control(raw)
    assert m.w_soc_mult == 2.0 and m.w_imbalance_mult == 0.5  # clamped to [0.5,2]
    assert m.lr == 1e-3 and m.entropy_coef == 0.05            # clamped to ceilings
    assert m.kl_target == 0.005 and m.mode_reg_coef == 1.0    # clamped to bounds


def test_parse_meta_control_defaults_when_absent_or_garbage():
    assert parse_meta_control('{"risk_budget": 0.7}') == MetaControl()  # no meta block
    assert parse_meta_control("not json at all") == MetaControl()       # tolerant, no raise


@pytest.mark.asyncio
async def test_llm_strategist_refresh_parses_and_caches():
    class FakeClient:
        async def chat(self, messages, *, temperature=0.2):
            return ('{"risk_budget": 0.3, "soc_target": 0.45, "avoid_modes": ["aggressive_taker"],'
                    ' "meta_control": {"w_soc_mult": 1.5}}')

    s = LLMStrategist(client=FakeClient())
    assert s.current_guidance() is None and s.current_meta() is None
    g = await s.arefresh(recent_pnl=[1.0, -0.5], soc_frac=0.5, best_bid=49.0, best_ask=51.0, last_price=50.0)
    assert g.risk_budget == 0.3
    assert s.current_guidance() is g
    assert s.current_meta().w_soc_mult == 1.5  # meta cached alongside guidance
    assert s.reflection_log[-1]["meta_control"]["w_soc_mult"] == 1.5


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
