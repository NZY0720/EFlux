"""Unit tests for the strategy compiler and primitive library.

Covers the deterministic expansion of StrategyAction -> intents, the cancel/reprice
policy, and the key acceptance criterion: the neutral LIQUIDATE_SURPLUS / COVER_DEFICIT
primitives reproduce the Truthful agent's balance quote (the structured language can
encode the existing baseline).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot, OpenOrderView
from eflux.agents.strategy import OrderProgramCompiler, StrategyAction, StrategyMode
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.valuation import TruthfulValuationOracle
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _make_ctx(*, pv_kw: float, load_kw: float, soc_kwh: float = 5.0, markup_floor: float = 0.0) -> AgentContext:
    params = VPPParams(markup_floor=markup_floor)
    state = VPPState(sim_ts=datetime.now(UTC), soc_kwh=soc_kwh, pv_kw=pv_kw, load_kw=load_kw)
    state.update_net()
    state.pending_net_kwh = state.net_kw * 1.0
    market = MarketSnapshot(
        sim_ts=state.sim_ts,
        best_bid=Decimal("48"),
        best_ask=Decimal("52"),
        last_price=Decimal("50"),
        mid_price=Decimal("50"),
    )
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(kw_peak=params.pv_kw_peak),
        battery=Battery(capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max, soc_kwh=soc_kwh),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=market,
        rng=random.Random(0),
        tick_duration_h=1.0,
    )


def _compile(ctx, action, *, price_ref="50.0", demand_beta=0.0):
    sig = TruthfulValuationOracle(price_ref=Decimal(price_ref), demand_beta=demand_beta).estimate(ctx)
    return OrderProgramCompiler().compile(ctx, action, sig)


# --- Parity with Truthful ---------------------------------------------------

def test_liquidate_surplus_matches_truthful():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS))
    truthful = TruthfulAgent(price_ref=Decimal("50.0")).decide(ctx)
    assert len(compiled.order_intents) == 1
    o, t = compiled.order_intents[0], truthful[0]
    assert (o.side, o.price, o.qty, o.dispatched) == ("sell", t.price, t.qty, False)
    assert o.price == Decimal("5.0000") and o.qty == Decimal("4.0000")


def test_cover_deficit_matches_truthful():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.COVER_DEFICIT))
    truthful = TruthfulAgent(price_ref=Decimal("50.0")).decide(ctx)
    assert len(compiled.order_intents) == 1
    o, t = compiled.order_intents[0], truthful[0]
    assert (o.side, o.price, o.qty) == ("buy", t.price, t.qty)
    assert o.price == Decimal("50.0000") and o.qty == Decimal("2.5000")


def test_cover_deficit_scarcity_matches_truthful():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)
    ctx.open_orders_net_kwh = -5.0
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.COVER_DEFICIT), demand_beta=0.5)
    truthful = TruthfulAgent(price_ref=Decimal("50.0"), demand_beta=0.5).decide(ctx)
    assert compiled.order_intents[0].price == truthful[0].price == Decimal("68.7500")


# --- Primitive behaviour ----------------------------------------------------

def test_noop_and_hold_emit_nothing():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0)
    assert _compile(ctx, StrategyAction(mode=StrategyMode.NOOP)).is_empty
    assert _compile(ctx, StrategyAction(mode=StrategyMode.HOLD_ENERGY)).is_empty


def test_qty_fraction_scales_quantity():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, qty_fraction=0.5))
    assert compiled.order_intents[0].qty == Decimal("2.0000")  # half of 4.0


def test_aggressiveness_crosses_toward_best_bid():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=2.0)  # fair sell = 100, above best_bid 48
    passive = _compile(ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS)).order_intents[0]
    aggressive = _compile(
        ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, aggressiveness=1.0)
    ).order_intents[0]
    assert passive.price == Decimal("100.0000")
    assert aggressive.price == Decimal("48.0000")  # crossed fully to best_bid


def test_battery_arbitrage_sells_above_target_dispatched():
    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, soc_kwh=5.0)  # soc 0.5
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.BATTERY_ARBITRAGE, soc_target=0.4))
    o = compiled.order_intents[0]
    assert o.side == "sell" and o.dispatched is True
    assert Decimal("52") < o.price < Decimal("53")  # delivery cost 50/sqrt(0.9)
    assert o.qty == Decimal("1.0000")  # (0.5 - 0.4) * 10 kWh


def test_dust_orders_are_dropped():
    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0)  # balanced → surplus/deficit ~0
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS))
    assert compiled.is_empty


def test_ladder_sell_steps_prices_up():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=1.0)  # fair sell = 50
    compiled = _compile(
        ctx, StrategyAction(mode=StrategyMode.LADDER_SELL, ladder_levels=3, ladder_slope=0.1)
    )
    prices = [o.price for o in compiled.order_intents]
    assert prices == [Decimal("50.0000"), Decimal("55.0000"), Decimal("60.0000")]
    assert all(o.qty == Decimal("1.3333") for o in compiled.order_intents)  # 4.0 / 3


# --- Cancel / reprice -------------------------------------------------------

def test_cancel_reprice_replaces_same_side_resting_order():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)  # surplus → fresh sell
    ctx.open_orders = [
        OpenOrderView(order_id=7, side="sell", price=Decimal("60"), remaining_qty=Decimal("2"), age_ticks=5)
    ]
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.CANCEL_REPRICE, cancel_age_ticks=3))
    assert len(compiled.replace_intents) == 1
    assert compiled.replace_intents[0].order_id == 7
    assert compiled.replace_intents[0].new_price == Decimal("5.0000")
    assert not compiled.cancel_intents and not compiled.order_intents  # the spec was consumed by the replace


def test_cancel_reprice_cancels_unmatched_side_and_keeps_fresh_order():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)  # deficit → fresh buy spec
    ctx.open_orders = [
        OpenOrderView(order_id=9, side="sell", price=Decimal("60"), remaining_qty=Decimal("1"), age_ticks=10)
    ]
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.CANCEL_REPRICE, cancel_age_ticks=3))
    assert [c.order_id for c in compiled.cancel_intents] == [9]  # sell order has no fresh sell spec
    assert len(compiled.order_intents) == 1 and compiled.order_intents[0].side == "buy"
    assert not compiled.replace_intents


def test_cancel_age_threshold_skips_young_orders():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    ctx.open_orders = [
        OpenOrderView(order_id=1, side="sell", price=Decimal("60"), remaining_qty=Decimal("1"), age_ticks=1)
    ]
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.CANCEL_REPRICE, cancel_age_ticks=3))
    # age 1 < 3 → not stale → no cancel/replace; the fresh re-quote still posts.
    assert not compiled.cancel_intents and not compiled.replace_intents
    assert len(compiled.order_intents) == 1


def test_as_intent_list_orders_cancels_then_orders():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)
    ctx.open_orders = [
        OpenOrderView(order_id=9, side="sell", price=Decimal("60"), remaining_qty=Decimal("1"), age_ticks=10)
    ]
    flat = _compile(ctx, StrategyAction(mode=StrategyMode.CANCEL_REPRICE, cancel_age_ticks=3)).as_intent_list()
    from eflux.agents.base import CancelIntent, OrderIntent

    assert isinstance(flat[0], CancelIntent) and isinstance(flat[-1], OrderIntent)
