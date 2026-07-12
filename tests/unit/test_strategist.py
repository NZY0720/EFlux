"""Structured LLM guidance tests (M6): parsing, soft application, and the async refresh."""

from __future__ import annotations

import pytest

from eflux.agents.reflective.strategist import (
    GuidanceParseError,
    LLMStrategist,
    MetaControl,
    StrategyGuidance,
    allowed_modes_for_market,
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
     "mode_pin": "cover_deficit", "halt": true, "passive_only": true, "price_bias_bps": -25.0,
     "lesson": "Crossed the spread too often last window."}
    """
    g = parse_guidance(raw)
    assert g.preferred_modes == (StrategyMode.LADDER_SELL, StrategyMode.PASSIVE_MARKET_MAKE)
    assert g.avoid_modes == (StrategyMode.AGGRESSIVE_TAKER,)
    assert g.mode_pin is StrategyMode.COVER_DEFICIT
    assert g.halt is True
    assert g.passive_only is True
    assert g.risk_budget == 0.4 and g.soc_target == 0.55
    assert g.price_bias_bps == -25.0
    assert "maker" in g.execution_style


def test_parse_guidance_clamps_out_of_range_and_drops_unknown_modes():
    g = parse_guidance(
        '{"preferred_modes": ["bogus", "noop"], "mode_pin": "not-a-mode",'
        ' "risk_budget": 5.0, "price_bias_bps": 500.0, "soc_target": -1.0}'
    )
    assert g.preferred_modes == (StrategyMode.NOOP,)  # unknown silently dropped
    assert g.mode_pin is None
    assert g.risk_budget == 1.5 and g.soc_target == 0.0
    assert g.price_bias_bps == 200.0


def test_parse_guidance_handles_code_fences():
    g = parse_guidance('```json\n{"risk_budget": 0.7}\n```')
    assert g.risk_budget == 0.7
    assert g.soc_target is None


def test_realprice_prompt_uses_grid_price_taker_context():
    prompt = build_strategist_system_prompt(market_mode="realprice").lower()
    assert "no peer order book" in prompt
    assert "caiso" in prompt and "grid" in prompt
    assert "grid_charge_on_dip" in prompt
    assert "grid_discharge_on_peak" in prompt
    assert "wait_for_better" in prompt
    assert "continuous double auction" not in prompt
    assert "order book depth" not in prompt
    assert "passive_market_make" not in prompt


def test_prompts_describe_binding_extreme_regime_levers():
    p2p = build_strategist_system_prompt(market_mode="p2p")
    realprice = build_strategist_system_prompt(market_mode="realprice")
    for prompt in (p2p, realprice):
        assert '"halt":         <bool; BINDING: place no new orders' in prompt
        assert "does NOT itself charge the battery" in prompt
        assert "never stores energy by itself" in prompt
        assert "Read `regime_note` in the input and act on extremes" in prompt
        assert "omit soc_target to preserve the executor's" in prompt
    # passive_only is a book-market lever: BINDING maker-only in p2p, inert in realprice.
    assert '"passive_only": <bool; BINDING: maker-only, never cross the spread' in p2p
    assert "mode_pin, halt, passive_only, and avoid_modes are BINDING" in p2p
    assert '"passive_only": <bool; no effect in this market' in realprice
    assert "mode_pin, halt, and avoid_modes are BINDING" in realprice
    assert 'prefer "passive_market_make"' in p2p
    assert 'prefer "passive_market_make"' not in realprice
    # Realprice persona must not surface order-book regime language.
    for stale in ("Thin/illiquid book", "bids elevated, few/no asks", "Book-specific"):
        assert stale not in realprice


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
        regime_note="balanced market",
        market_mode="p2p",
    )
    data = json.loads(msg)
    assert data["market_mode"] == "p2p"
    assert data["best_bid"] == 48.0 and data["best_ask"] == 52.0
    assert data["regime_note"] == "balanced market"
    assert "grid_raw_lmp" not in data


def test_realprice_guidance_drops_book_specific_preferred_modes():
    raw = """{"preferred_modes": ["passive_market_make", "ladder_sell", "cancel_reprice",
      "battery_arbitrage"], "mode_pin": "passive_market_make", "avoid_modes": ["ladder_buy"]}"""
    g = parse_guidance(raw, market_mode="realprice")
    assert g.preferred_modes == (StrategyMode.BATTERY_ARBITRAGE,)
    assert g.mode_pin is None
    # Avoid modes are still useful as soft "do not lean this way" explanations.
    assert g.avoid_modes == (StrategyMode.LADDER_BUY,)


def test_market_mode_allowed_sets_and_sanitization_block_wrong_primitives():
    assert StrategyMode.GRID_CHARGE_ON_DIP in allowed_modes_for_market("realprice")
    assert StrategyMode.PASSIVE_MARKET_MAKE not in allowed_modes_for_market("realprice")
    assert StrategyMode.GRID_CHARGE_ON_DIP not in allowed_modes_for_market("p2p")
    assert StrategyMode.PASSIVE_MARKET_MAKE in allowed_modes_for_market("p2p")

    p2p = parse_guidance(
        '{"preferred_modes": ["grid_charge_on_dip", "liquidate_surplus"],'
        ' "mode_pin": "wait_for_better"}',
        market_mode="p2p",
    )
    assert p2p.preferred_modes == (StrategyMode.LIQUIDATE_SURPLUS,)
    assert p2p.mode_pin is None

    realprice = parse_guidance(
        '{"preferred_modes": ["grid_discharge_on_peak", "ladder_sell"],'
        ' "mode_pin": "cancel_reprice"}',
        market_mode="realprice",
    )
    assert realprice.preferred_modes == (StrategyMode.GRID_DISCHARGE_ON_PEAK,)
    assert realprice.mode_pin is None


def test_parse_guidance_raises_on_garbage():
    with pytest.raises(GuidanceParseError):
        parse_guidance("no json here at all")


def test_apply_guidance_scales_size_by_risk_budget():
    a = StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, qty_fraction=1.0, aggressiveness=0.8)
    out = apply_guidance(a, StrategyGuidance(risk_budget=0.5, soc_target=0.6))
    assert out.qty_fraction == 0.5 and out.aggressiveness == pytest.approx(0.4)
    assert out.soc_target == 0.6
    assert out.mode is StrategyMode.LIQUIDATE_SURPLUS  # primitive untouched


def test_guidance_without_soc_target_preserves_executor_target():
    action = StrategyAction(mode=StrategyMode.BATTERY_ARBITRAGE, soc_target=0.9)

    parsed = parse_guidance('{"risk_budget": 0.5}')
    assert parsed.soc_target is None
    assert apply_guidance(action, parsed).soc_target == 0.9
    assert apply_guidance(action, StrategyGuidance(soc_target=0.2)).soc_target == 0.2


def test_parse_guidance_round_trips_binding_levers():
    g = parse_guidance('{"halt": true, "passive_only": "true"}')

    assert g.halt is True
    assert g.passive_only is True


def test_apply_guidance_halt_forces_hold_energy():
    a = StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, qty_fraction=1.0, aggressiveness=0.8)
    out = apply_guidance(a, StrategyGuidance(halt=True, risk_budget=1.0))

    assert out.mode is StrategyMode.HOLD_ENERGY
    assert out.qty_fraction == 1.0
    assert out.aggressiveness == pytest.approx(0.8)


def test_apply_guidance_passive_only_forces_zero_aggressiveness():
    a = StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, qty_fraction=1.0, aggressiveness=0.8)
    out = apply_guidance(a, StrategyGuidance(passive_only=True, risk_budget=1.5))

    assert out.mode is StrategyMode.LIQUIDATE_SURPLUS
    assert out.qty_fraction == 1.5
    assert out.aggressiveness == 0.0


def test_apply_guidance_mode_pin_is_binding_and_wins_over_halt_and_avoid():
    a = StrategyAction(
        mode=StrategyMode.AGGRESSIVE_TAKER,
        qty_fraction=1.0,
        aggressiveness=0.8,
        price_offset_bps=10.0,
    )
    out = apply_guidance(
        a,
        StrategyGuidance(
            avoid_modes=(StrategyMode.AGGRESSIVE_TAKER,),
            mode_pin=StrategyMode.BATTERY_ARBITRAGE,
            halt=True,
            risk_budget=1.5,
            price_bias_bps=20.0,
        ),
    )
    assert out.mode is StrategyMode.BATTERY_ARBITRAGE
    assert out.qty_fraction == 1.5
    assert out.aggressiveness == 1.0
    assert out.price_offset_bps == 30.0


def test_apply_guidance_vetoes_avoided_mode():
    a = StrategyAction(mode=StrategyMode.AGGRESSIVE_TAKER, qty_fraction=1.0)
    out = apply_guidance(
        a, StrategyGuidance(avoid_modes=(StrategyMode.AGGRESSIVE_TAKER,), risk_budget=1.0)
    )

    assert out.mode is StrategyMode.HOLD_ENERGY
    assert out.qty_fraction == 1.0


def test_apply_guidance_none_is_identity():
    a = StrategyAction(mode=StrategyMode.COVER_DEFICIT, qty_fraction=0.7)
    assert apply_guidance(a, None) == a


def test_parse_meta_control_extracts_and_clamps():
    raw = """{"risk_budget": 0.5,
      "meta_control": {"w_soc_mult": 5.0, "w_imbalance_mult": 0.1, "lr": 1e-2,
                       "entropy_coef": 0.2, "kl_target": 0.001, "mode_reg_coef": 2.0}}"""
    m = parse_meta_control(raw)
    assert m.w_soc_mult == 2.0 and m.w_imbalance_mult == 0.5  # clamped to [0.5,2]
    assert m.lr == 1e-3 and m.entropy_coef == 0.05  # clamped to ceilings
    assert m.kl_target == 0.005 and m.mode_reg_coef == 1.0  # clamped to bounds


def test_parse_meta_control_defaults_when_absent_or_garbage():
    assert parse_meta_control('{"risk_budget": 0.7}') == MetaControl()  # no meta block
    assert parse_meta_control("not json at all") == MetaControl()  # tolerant, no raise


@pytest.mark.asyncio
async def test_llm_strategist_refresh_parses_and_caches():
    class FakeClient:
        async def chat(self, messages, *, temperature=0.2):
            return (
                '{"risk_budget": 0.3, "soc_target": 0.45, "avoid_modes": ["aggressive_taker"],'
                ' "meta_control": {"w_soc_mult": 1.5}}'
            )

    s = LLMStrategist(client=FakeClient())
    assert s.current_guidance() is None and s.current_meta() is None
    g = await s.arefresh(
        recent_pnl=[1.0, -0.5], soc_frac=0.5, best_bid=49.0, best_ask=51.0, last_price=50.0
    )
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


@pytest.mark.asyncio
async def test_llm_strategist_strict_mode_raises_on_failure():
    class BadClient:
        async def chat(self, messages, *, temperature=0.2):
            raise RuntimeError("endpoint down")

    s = LLMStrategist(client=BadClient(), raise_errors=True)
    with pytest.raises(RuntimeError, match="endpoint down"):
        await s.arefresh(recent_pnl=[], soc_frac=0.5, best_bid=None, best_ask=None, last_price=None)
    assert s.current_guidance() is None
    assert s.fail_count == 1
