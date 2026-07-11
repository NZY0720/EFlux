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
from eflux.agents.strategy.schema import PRICE_MULT_MAX, PRICE_MULT_MIN
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.valuation import TruthfulValuationOracle, ValuationSignal
from eflux.data.electricity_market import synthetic_quote
from eflux.vpp.base import VPPParams, VPPState
from eflux.vpp.der import PV, Battery, FlexibleLoad


def _make_ctx(
    *,
    pv_kw: float,
    load_kw: float,
    soc_kwh: float = 5.0,
    markup_floor: float = 0.0,
    market_mode: str = "p2p",
    external_price: Decimal | None = None,
) -> AgentContext:
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
        market_mode=market_mode,
        external_market=None
        if external_price is None
        else synthetic_quote(price=external_price, now=state.sim_ts),
    )
    return AgentContext(
        vpp_id=1,
        params=params,
        state=state,
        pv=PV(kw_peak=params.pv_kw_peak),
        battery=Battery(
            capacity_kwh=params.battery_kwh, max_power_kw=params.battery_kw_max, soc_kwh=soc_kwh
        ),
        load=FlexibleLoad(base_kw=params.load_kw_base),
        market=market,
        rng=random.Random(0),
        tick_duration_h=1.0,
    )


def _compile(ctx, action, *, price_ref="50.0", demand_beta=0.0):
    sig = TruthfulValuationOracle(price_ref=Decimal(price_ref), demand_beta=demand_beta).estimate(
        ctx
    )
    return OrderProgramCompiler().compile(ctx, action, sig)


def _grid_valuation(
    *,
    soc: float = 0.5,
    expected_12h: float | None = 100.0,
    expected_1h: float | None = None,
) -> ValuationSignal:
    return ValuationSignal(
        fair_buy_price=100.0,
        fair_sell_price=100.0,
        marginal_battery_value=100.0,
        battery_sell_price=100.0,
        battery_buy_price=100.0,
        surplus_kwh=0.0,
        deficit_kwh=0.0,
        soc_frac=soc,
        soc_pressure=soc - 0.5,
        expected_ref_1h=expected_1h,
        expected_ref_12h=expected_12h,
        price_trend=0.0,
    )


def _compile_with_signal(ctx: AgentContext, action: StrategyAction, sig: ValuationSignal):
    return OrderProgramCompiler().compile(ctx, action, sig)


# --- Parity with Truthful ---------------------------------------------------


def test_liquidate_surplus_matches_truthful():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS))
    truthful = TruthfulAgent(price_ref=Decimal("50.0")).decide(ctx)
    assert len(compiled.order_requests) == 1
    o, t = compiled.order_requests[0], truthful.orders[0]
    assert (o.side, o.price, o.qty_kwh, o.purpose.value) == (
        "sell",
        t.price,
        t.qty_kwh,
        "balance",
    )
    assert o.price == Decimal("5.0000") and o.qty_kwh == Decimal("4.0000")


def test_cover_deficit_matches_truthful():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.COVER_DEFICIT))
    truthful = TruthfulAgent(price_ref=Decimal("50.0")).decide(ctx)
    assert len(compiled.order_requests) == 1
    o, t = compiled.order_requests[0], truthful.orders[0]
    assert (o.side, o.price, o.qty_kwh) == ("buy", t.price, t.qty_kwh)
    assert o.price == Decimal("50.0000") and o.qty_kwh == Decimal("2.5000")


def test_cover_deficit_scarcity_matches_truthful():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)
    ctx.state.pending_net_kwh = -7.5
    ctx.open_orders_net_kwh = -5.0
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.COVER_DEFICIT), demand_beta=0.5)
    truthful = TruthfulAgent(price_ref=Decimal("50.0"), demand_beta=0.5).decide(ctx)
    assert compiled.order_requests[0].price == truthful.orders[0].price == Decimal("75.0000")
    assert compiled.order_requests[0].qty_kwh == truthful.orders[0].qty_kwh == Decimal("2.5000")


# --- Primitive behaviour ----------------------------------------------------


def test_noop_and_hold_emit_nothing():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0)
    assert _compile(ctx, StrategyAction(mode=StrategyMode.NOOP)).is_empty
    assert _compile(ctx, StrategyAction(mode=StrategyMode.HOLD_ENERGY)).is_empty


def test_grid_charge_on_dip_buys_only_below_forecast_threshold():
    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, market_mode="realprice", external_price=Decimal("96"))
    action = StrategyAction(mode=StrategyMode.GRID_CHARGE_ON_DIP, soc_target=0.9)
    compiled = _compile_with_signal(ctx, action, _grid_valuation(expected_12h=100.0))
    assert compiled.is_empty

    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, market_mode="realprice", external_price=Decimal("80"))
    compiled = _compile_with_signal(ctx, action, _grid_valuation(expected_12h=100.0))
    o = compiled.order_requests[0]
    assert (o.side, o.purpose.value) == ("buy", "battery")
    assert o.price >= Decimal("80.0000")
    assert o.qty_kwh == Decimal("0.2500")


def test_grid_discharge_on_peak_sells_only_above_forecast_threshold():
    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, market_mode="realprice", external_price=Decimal("104"))
    action = StrategyAction(mode=StrategyMode.GRID_DISCHARGE_ON_PEAK, soc_target=0.2)
    compiled = _compile_with_signal(ctx, action, _grid_valuation(expected_12h=100.0))
    assert compiled.is_empty

    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, market_mode="realprice", external_price=Decimal("120"))
    compiled = _compile_with_signal(ctx, action, _grid_valuation(expected_12h=100.0))
    o = compiled.order_requests[0]
    assert (o.side, o.purpose.value) == ("sell", "battery")
    assert Decimal("0") < o.price <= Decimal("120.0000")
    assert o.qty_kwh == Decimal("0.2500")


def test_grid_modes_noop_without_realprice_or_threshold_data_and_wait_holds():
    p2p_ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, market_mode="p2p", external_price=Decimal("80"))
    action = StrategyAction(mode=StrategyMode.GRID_CHARGE_ON_DIP, soc_target=0.9)
    assert _compile_with_signal(p2p_ctx, action, _grid_valuation(expected_12h=100.0)).is_empty

    real_ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, market_mode="realprice", external_price=None)
    assert _compile_with_signal(
        real_ctx, action, _grid_valuation(expected_12h=None, expected_1h=None)
    ).is_empty
    assert _compile_with_signal(
        real_ctx, StrategyAction(mode=StrategyMode.WAIT_FOR_BETTER), _grid_valuation()
    ).is_empty


def test_qty_fraction_scales_quantity():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, qty_fraction=0.5))
    assert compiled.order_requests[0].qty_kwh == Decimal("2.0000")


def test_imbalance_modes_do_not_oversell_or_overbuy_above_one_qty_fraction():
    surplus = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    liquidate = _compile(
        surplus, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, qty_fraction=1.5)
    ).order_requests[0]
    taker_sell = _compile(
        surplus, StrategyAction(mode=StrategyMode.AGGRESSIVE_TAKER, qty_fraction=1.5)
    ).order_requests[0]

    deficit = _make_ctx(pv_kw=0.5, load_kw=3.0)
    cover = _compile(
        deficit, StrategyAction(mode=StrategyMode.COVER_DEFICIT, qty_fraction=1.5)
    ).order_requests[0]
    taker_buy = _compile(
        deficit, StrategyAction(mode=StrategyMode.AGGRESSIVE_TAKER, qty_fraction=1.5)
    ).order_requests[0]

    assert liquidate.qty_kwh == Decimal("4.0000")
    assert taker_sell.qty_kwh == Decimal("4.0000")
    assert cover.qty_kwh == Decimal("2.5000")
    assert taker_buy.qty_kwh == Decimal("2.5000")


def test_battery_qty_fraction_above_one_is_not_imbalance_capped():
    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, soc_kwh=5.0)
    compiled = _compile(
        ctx,
        StrategyAction(
            mode=StrategyMode.BATTERY_ARBITRAGE,
            qty_fraction=1.5,
            soc_target=0.4,
        ),
    )
    assert compiled.order_requests[0].qty_kwh == Decimal("0.2500")


def test_price_target_mult_scales_surplus_sell_anchor():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=1.0)  # fair sell = 50
    compiled = _compile(
        ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, price_target_mult=1.5)
    )
    assert compiled.order_requests[0].price == Decimal("75.0000")


def test_price_target_mult_scales_deficit_buy_anchor():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)  # fair buy = 50
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.COVER_DEFICIT, price_target_mult=0.8))
    assert compiled.order_requests[0].price == Decimal("40.0000")


def test_price_target_mult_is_clamped_to_policy_bounds():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=1.0)  # fair sell = 50
    low = _compile(
        ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, price_target_mult=0.01)
    ).order_requests[0]
    high = _compile(
        ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, price_target_mult=10.0)
    ).order_requests[0]
    assert low.price == Decimal("50.0000") * Decimal(str(PRICE_MULT_MIN))
    assert high.price == Decimal("50.0000") * Decimal(str(PRICE_MULT_MAX))


def test_price_target_mult_none_keeps_legacy_price():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=1.0)
    legacy = _compile(ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS)).order_requests[0]
    explicit_none = _compile(
        ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, price_target_mult=None)
    ).order_requests[0]
    assert explicit_none.price == legacy.price


def test_aggressiveness_crosses_toward_best_bid():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=2.0)  # fair sell = 100, above best_bid 48
    passive = _compile(ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS)).order_requests[0]
    aggressive = _compile(
        ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS, aggressiveness=1.0)
    ).order_requests[0]
    assert passive.price == Decimal("100.0000")
    assert aggressive.price == Decimal("48.0000")  # crossed fully to best_bid


def test_aggressive_taker_price_target_mult_limits_sell_cross():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=1.0)  # fair sell = 50
    compiled = _compile(
        ctx, StrategyAction(mode=StrategyMode.AGGRESSIVE_TAKER, price_target_mult=1.1)
    )
    assert compiled.order_requests[0].side == "sell"
    assert compiled.order_requests[0].price == Decimal("55.0000")


def test_aggressive_taker_price_target_mult_limits_buy_cross():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)  # fair buy = 50, best ask = 52
    compiled = _compile(
        ctx, StrategyAction(mode=StrategyMode.AGGRESSIVE_TAKER, price_target_mult=0.9)
    )
    assert compiled.order_requests[0].side == "buy"
    assert compiled.order_requests[0].price == Decimal("45.0000")


def test_battery_arbitrage_sells_above_target_dispatched():
    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0, soc_kwh=5.0)  # soc 0.5
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.BATTERY_ARBITRAGE, soc_target=0.4))
    o = compiled.order_requests[0]
    assert o.side == "sell" and o.purpose.value == "battery"
    assert Decimal("52") < o.price < Decimal("53")  # delivery cost 50/sqrt(0.9)
    assert o.qty_kwh == Decimal("0.2500")  # capped by 3 kW over five minutes


def test_dust_orders_are_dropped():
    ctx = _make_ctx(pv_kw=2.0, load_kw=2.0)  # balanced → surplus/deficit ~0
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS))
    assert compiled.is_empty


def test_ladder_sell_steps_prices_up():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=1.0)  # fair sell = 50
    compiled = _compile(
        ctx, StrategyAction(mode=StrategyMode.LADDER_SELL, ladder_levels=3, ladder_slope=0.1)
    )
    prices = [o.price for o in compiled.order_requests]
    assert prices == [Decimal("50.0000"), Decimal("55.0000"), Decimal("60.0000")]
    assert all(o.qty_kwh == Decimal("1.3333") for o in compiled.order_requests)


# --- Cancel / reprice -------------------------------------------------------


def test_cancel_reprice_replaces_same_side_resting_order():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)  # surplus → fresh sell
    ctx.open_orders = [
        OpenOrderView(
            order_id=7, side="sell", price=Decimal("60"), remaining_qty=Decimal("2"), age_ticks=5
        )
    ]
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.CANCEL_REPRICE, cancel_age_ticks=3))
    assert len(compiled.replace_requests) == 1
    assert compiled.replace_requests[0].order_id == 7
    assert compiled.replace_requests[0].replacement.price == Decimal("5.0000")
    assert not compiled.cancel_requests and not compiled.order_requests


def test_cancel_reprice_cancels_unmatched_side_and_keeps_fresh_order():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)  # deficit → fresh buy spec
    ctx.open_orders = [
        OpenOrderView(
            order_id=9, side="sell", price=Decimal("60"), remaining_qty=Decimal("1"), age_ticks=10
        )
    ]
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.CANCEL_REPRICE, cancel_age_ticks=3))
    assert [c.order_id for c in compiled.cancel_requests] == [9]
    assert len(compiled.order_requests) == 1 and compiled.order_requests[0].side == "buy"
    assert not compiled.replace_requests


def test_cancel_age_threshold_skips_young_orders():
    ctx = _make_ctx(pv_kw=5.0, load_kw=1.0, markup_floor=0.1)
    ctx.open_orders = [
        OpenOrderView(
            order_id=1, side="sell", price=Decimal("60"), remaining_qty=Decimal("1"), age_ticks=1
        )
    ]
    compiled = _compile(ctx, StrategyAction(mode=StrategyMode.CANCEL_REPRICE, cancel_age_ticks=3))
    # age 1 < 3 → not stale → no cancel/replace; the fresh re-quote still posts.
    assert not compiled.cancel_requests and not compiled.replace_requests
    assert len(compiled.order_requests) == 1


def test_as_decision_preserves_cancel_and_order_requests():
    ctx = _make_ctx(pv_kw=0.5, load_kw=3.0)
    ctx.open_orders = [
        OpenOrderView(
            order_id=9, side="sell", price=Decimal("60"), remaining_qty=Decimal("1"), age_ticks=10
        )
    ]
    decision = _compile(
        ctx, StrategyAction(mode=StrategyMode.CANCEL_REPRICE, cancel_age_ticks=3)
    ).as_decision()
    assert [cancel.order_id for cancel in decision.cancels] == [9]
    assert len(decision.orders) == 1 and decision.orders[0].side == "buy"
