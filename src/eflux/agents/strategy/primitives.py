"""Scripted strategy primitive library.

`build_program(action, ctx, valuation)` maps a `StrategyAction` (a primitive + its
parameters) to a deterministic `OrderProgram`, pricing every order off the
`ValuationSignal` so primitives stay economically grounded. This is the bridge between
the abstract action space and concrete orders; it is pure (no market calls, no agent
state) and independently testable.

With the neutral action (`aggressiveness=0`, `qty_fraction=1`, `price_offset_bps=0`),
`LIQUIDATE_SURPLUS` / `COVER_DEFICIT` reproduce the Truthful agent's balance quote — the
proof that the structured language can encode the existing baseline (design note §4).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.decision import SilenceReason
from eflux.agents.strategy.schema import (
    PRICE_MULT_MAX,
    PRICE_MULT_MIN,
    CancelPolicy,
    OrderProgram,
    OrderSpec,
    StrategyAction,
    StrategyMode,
)
from eflux.agents.valuation import ValuationSignal
from eflux.market.delivery import OrderPurpose

QUANT = Decimal("0.0001")
GRID_PRICE_MARGIN = 0.05


def _q(x: float | Decimal) -> Decimal:
    return (x if isinstance(x, Decimal) else Decimal(str(x))).quantize(QUANT)


def _qty(x: float | Decimal) -> Decimal:
    value = x if isinstance(x, Decimal) else Decimal(str(x))
    return value.quantize(QUANT, rounding=ROUND_DOWN)


def _anchor(base: float | Decimal, action: StrategyAction) -> Decimal:
    """Apply the action's optional price-target multiplier to a valuation anchor."""
    base = base if isinstance(base, Decimal) else Decimal(str(base))
    if action.price_target_mult is None:
        return base
    mult = max(PRICE_MULT_MIN, min(PRICE_MULT_MAX, float(action.price_target_mult)))
    return base * Decimal(str(mult))


def _effective_price(
    side: str, base: float | Decimal, action: StrategyAction, market: MarketSnapshot
) -> Decimal:
    """Move a base (fair) price toward the opposite best quote by `aggressiveness`
    (0 = rest as maker, 1 = cross to take), then apply the fine bps offset. Sells
    move down (improve fill), buys move up."""
    base = base if isinstance(base, Decimal) else Decimal(str(base))
    aggr = Decimal(str(action.aggressiveness))
    px = base
    if side == "sell" and market.best_bid is not None:
        target = min(base, market.best_bid)
        px = base - aggr * (base - target)
    elif side == "buy" and market.best_ask is not None:
        target = max(base, market.best_ask)
        px = base + aggr * (target - base)
    bps = Decimal(str(action.price_offset_bps)) / Decimal("10000")
    px = px * (Decimal("1") - bps) if side == "sell" else px * (Decimal("1") + bps)
    return px.quantize(QUANT)


def _battery_qty_kwh(
    ctx: AgentContext, valuation: ValuationSignal, action: StrategyAction, side: str
) -> float:
    """Terminal energy toward SOC target, capped by delivery-interval power."""
    cap = ctx.battery.capacity_kwh
    soc = valuation.soc_frac
    cell_head = (
        (soc - action.soc_target) * cap if side == "sell" else (action.soc_target - soc) * cap
    )
    cell_head = max(0.0, cell_head)
    eta = max(0.01, ctx.battery.eta_rt) ** 0.5
    terminal_head = cell_head * eta if side == "sell" else cell_head / eta
    interval_limit = ctx.battery.max_power_kw * ctx.primary_interval.duration_h
    # Risk scaling may exceed 1.0, but it can only use more SOC headroom; it
    # must never scale beyond the interval's inverter energy budget.
    return min(terminal_head * max(0.0, action.qty_fraction), interval_limit)


def _imbalance_qty_fraction(action: StrategyAction) -> float:
    """Imbalance primitives cannot quote beyond the current surplus/deficit."""
    return min(1.0, max(0.0, action.qty_fraction))


def _positive(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out > 0.0 else None


def _market_mid(ctx: AgentContext) -> float | None:
    return _positive(ctx.market.mid_price) or _positive(ctx.market.last_price)


def _current_grid_buy_price(ctx: AgentContext, valuation: ValuationSignal) -> float | None:
    quote = ctx.market.external_market
    return (
        _positive(getattr(quote, "import_price", None))
        or _positive(valuation.fair_buy_price)
        or _market_mid(ctx)
    )


def _current_grid_sell_price(ctx: AgentContext, valuation: ValuationSignal) -> float | None:
    quote = ctx.market.external_market
    return (
        _positive(getattr(quote, "export_price", None))
        or _positive(valuation.fair_sell_price)
        or _market_mid(ctx)
    )


def _forecast_reference(ctx: AgentContext, valuation: ValuationSignal) -> float | None:
    return (
        _positive(valuation.expected_ref_12h)
        or _positive(valuation.expected_ref_1h)
        or _positive(valuation.marginal_battery_value)
        or _market_mid(ctx)
        or _positive(
            0.5 * (float(valuation.fair_buy_price or 0.0) + float(valuation.fair_sell_price or 0.0))
        )
    )


def build_program(
    action: StrategyAction, ctx: AgentContext, valuation: ValuationSignal
) -> OrderProgram:
    mode = action.mode
    market = ctx.market

    if mode in (StrategyMode.NOOP, StrategyMode.HOLD_ENERGY, StrategyMode.WAIT_FOR_BETTER):
        return OrderProgram(mode=mode, orders=[])

    if mode == StrategyMode.GRID_CHARGE_ON_DIP:
        if market.market_mode != "realprice":
            return OrderProgram(mode=mode, orders=[])
        current = _current_grid_buy_price(ctx, valuation)
        reference = _forecast_reference(ctx, valuation)
        if current is None or reference is None or current >= reference * (1.0 - GRID_PRICE_MARGIN):
            return OrderProgram(mode=mode, orders=[])
        qty = _battery_qty_kwh(ctx, valuation, action, "buy")
        if qty <= 0:
            return OrderProgram(mode=mode, rationale=SilenceReason.ZERO_HEADROOM)
        price = max(_anchor(current, action), _q(current))
        return OrderProgram(
            mode,
            orders=_one("buy", price, qty, action, purpose=OrderPurpose.BATTERY),
        )

    if mode == StrategyMode.GRID_DISCHARGE_ON_PEAK:
        if market.market_mode != "realprice":
            return OrderProgram(mode=mode, orders=[])
        current = _current_grid_sell_price(ctx, valuation)
        reference = _forecast_reference(ctx, valuation)
        if current is None or reference is None or current <= reference * (1.0 + GRID_PRICE_MARGIN):
            return OrderProgram(mode=mode, orders=[])
        qty = _battery_qty_kwh(ctx, valuation, action, "sell")
        if qty <= 0:
            return OrderProgram(mode=mode, rationale=SilenceReason.ZERO_HEADROOM)
        price = min(_anchor(current, action), _q(current))
        return OrderProgram(
            mode,
            orders=_one("sell", price, qty, action, purpose=OrderPurpose.BATTERY),
        )

    if mode == StrategyMode.LIQUIDATE_SURPLUS:
        qty = valuation.surplus_kwh * _imbalance_qty_fraction(action)
        price = _effective_price("sell", _anchor(valuation.fair_sell_price, action), action, market)
        return OrderProgram(
            mode,
            orders=_one("sell", price, qty, action, purpose=valuation.supply_purpose),
        )

    if mode == StrategyMode.COVER_DEFICIT:
        qty = valuation.deficit_kwh * _imbalance_qty_fraction(action)
        price = _effective_price("buy", _anchor(valuation.fair_buy_price, action), action, market)
        return OrderProgram(
            mode,
            orders=_one("buy", price, qty, action, purpose=OrderPurpose.BALANCE),
        )

    if mode == StrategyMode.BATTERY_ARBITRAGE:
        side = "sell" if valuation.soc_frac >= action.soc_target else "buy"
        base = valuation.battery_sell_price if side == "sell" else valuation.battery_buy_price
        qty = _battery_qty_kwh(ctx, valuation, action, side)
        if qty <= 0:
            return OrderProgram(mode=mode, rationale=SilenceReason.ZERO_HEADROOM)
        price = _effective_price(side, _anchor(base, action), action, market)
        return OrderProgram(
            mode,
            orders=_one(side, price, qty, action, purpose=OrderPurpose.BATTERY),
        )

    if mode == StrategyMode.AGGRESSIVE_TAKER:
        # Cross the spread on the imbalance side to take liquidity now.
        if valuation.surplus_kwh >= valuation.deficit_kwh:
            side, base, avail = "sell", valuation.fair_sell_price, valuation.surplus_kwh
            cross = market.best_bid
        else:
            side, base, avail = "buy", valuation.fair_buy_price, valuation.deficit_kwh
            cross = market.best_ask
        px = cross if cross is not None else Decimal(str(base))
        if action.price_target_mult is None:
            price = _effective_price(side, px, action, market)
        else:
            anchored = _anchor(base, action)
            limited = max(px, anchored) if side == "sell" else min(px, anchored)
            price = _effective_price(side, limited, action, market)
            price = (
                max(price, anchored.quantize(QUANT))
                if side == "sell"
                else min(price, anchored.quantize(QUANT))
            )
        purpose = valuation.supply_purpose if side == "sell" else OrderPurpose.BALANCE
        return OrderProgram(
            mode,
            orders=_one(
                side,
                price,
                avail * _imbalance_qty_fraction(action),
                action,
                purpose=purpose,
            ),
        )

    if mode in (StrategyMode.LADDER_SELL, StrategyMode.LADDER_BUY):
        return _ladder(mode, action, valuation)

    if mode == StrategyMode.PASSIVE_MARKET_MAKE:
        return _market_make(action, ctx, valuation)

    if mode == StrategyMode.CANCEL_REPRICE:
        return _cancel_reprice(action, ctx, valuation)

    return OrderProgram(mode, orders=[])


def _one(
    side: str,
    price: Decimal,
    qty: float,
    action: StrategyAction,
    *,
    purpose: OrderPurpose,
) -> list[OrderSpec]:
    if qty <= 0:
        return []
    return [
        OrderSpec(
            side=side,
            price=price,
            qty=_qty(qty),
            purpose=purpose,
            ttl_ticks=action.ttl_ticks,
        )
    ]


def _ladder(mode: StrategyMode, action: StrategyAction, valuation: ValuationSignal) -> OrderProgram:
    sell = mode == StrategyMode.LADDER_SELL
    avail = valuation.surplus_kwh if sell else valuation.deficit_kwh
    base = _anchor(valuation.fair_sell_price if sell else valuation.fair_buy_price, action)
    levels = max(1, action.ladder_levels)
    per = (avail * max(0.0, action.qty_fraction)) / levels
    slope = Decimal(str(action.ladder_slope))
    purpose = valuation.supply_purpose if sell else OrderPurpose.BALANCE
    specs: list[OrderSpec] = []
    for i in range(levels):
        # Sells step up (ask higher further out); buys step down (bid lower).
        factor = (Decimal("1") + slope * i) if sell else (Decimal("1") - slope * i)
        price = (base * factor).quantize(QUANT)
        if price > 0 and per > 0:
            specs.append(
                OrderSpec(
                    "sell" if sell else "buy",
                    price,
                    _qty(per),
                    purpose=purpose,
                    ttl_ticks=action.ttl_ticks,
                )
            )
    return OrderProgram(mode, orders=specs)


def _market_make(
    action: StrategyAction, ctx: AgentContext, valuation: ValuationSignal
) -> OrderProgram:
    """Two-sided maker quotes straddling the battery fair value, backed by the
    battery (dispatched). Half-spread comes from ladder_slope (default a few %)."""
    mid = _anchor(valuation.marginal_battery_value, action)
    half = Decimal(str(action.ladder_slope)) if action.ladder_slope > 0 else Decimal("0.02")
    sell_qty = _battery_qty_kwh(ctx, valuation, action, "sell")
    buy_qty = _battery_qty_kwh(ctx, valuation, action, "buy")
    if sell_qty <= 0 and buy_qty <= 0:
        return OrderProgram(action.mode, rationale=SilenceReason.ZERO_HEADROOM)
    specs: list[OrderSpec] = []
    ask = (mid * (Decimal("1") + half)).quantize(QUANT)
    bid = (mid * (Decimal("1") - half)).quantize(QUANT)
    if sell_qty > 0:
        specs.append(
            OrderSpec(
                "sell",
                ask,
                _qty(sell_qty),
                purpose=OrderPurpose.BATTERY,
                ttl_ticks=action.ttl_ticks,
            )
        )
    if buy_qty > 0:
        specs.append(
            OrderSpec(
                "buy",
                bid,
                _qty(buy_qty),
                purpose=OrderPurpose.BATTERY,
                ttl_ticks=action.ttl_ticks,
            )
        )
    return OrderProgram(action.mode, orders=specs)


def _cancel_reprice(
    action: StrategyAction, ctx: AgentContext, valuation: ValuationSignal
) -> OrderProgram:
    """Cancel/replace stale resting quotes and re-quote the current imbalance.
    The compiler pairs the fresh specs below with the cancelled orders."""
    fresh = build_program(
        StrategyAction(
            mode=StrategyMode.LIQUIDATE_SURPLUS
            if valuation.surplus_kwh >= valuation.deficit_kwh
            else StrategyMode.COVER_DEFICIT,
            aggressiveness=action.aggressiveness,
            qty_fraction=action.qty_fraction,
            price_offset_bps=action.price_offset_bps,
            price_target_mult=action.price_target_mult,
            ttl_ticks=action.ttl_ticks,
        ),
        ctx,
        valuation,
    )
    policy = CancelPolicy(cancel_age_ticks=max(1, action.cancel_age_ticks), reprice=True)
    return OrderProgram(
        StrategyMode.CANCEL_REPRICE, orders=list(fresh.orders), cancel_policy=policy
    )
