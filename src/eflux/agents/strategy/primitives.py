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

from decimal import Decimal

from eflux.agents.base import AgentContext, MarketSnapshot
from eflux.agents.strategy.schema import (
    CancelPolicy,
    OrderProgram,
    OrderSpec,
    StrategyAction,
    StrategyMode,
)
from eflux.agents.valuation import ValuationSignal

QUANT = Decimal("0.0001")


def _q(x: float | Decimal) -> Decimal:
    return (x if isinstance(x, Decimal) else Decimal(str(x))).quantize(QUANT)


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


def _battery_qty_kwh(ctx: AgentContext, valuation: ValuationSignal, action: StrategyAction, side: str) -> float:
    """SOC headroom toward soc_target, capped by one tick's battery throughput."""
    cap = ctx.battery.capacity_kwh
    soc = valuation.soc_frac
    head = (soc - action.soc_target) * cap if side == "sell" else (action.soc_target - soc) * cap
    head = max(0.0, head)
    per_tick = ctx.battery.max_power_kw * ctx.tick_duration_h
    return min(head, per_tick) * max(0.0, action.qty_fraction)


def build_program(action: StrategyAction, ctx: AgentContext, valuation: ValuationSignal) -> OrderProgram:
    mode = action.mode
    market = ctx.market

    if mode in (StrategyMode.NOOP, StrategyMode.HOLD_ENERGY):
        return OrderProgram(mode=mode, orders=[])

    if mode == StrategyMode.LIQUIDATE_SURPLUS:
        qty = valuation.surplus_kwh * max(0.0, action.qty_fraction)
        price = _effective_price("sell", valuation.fair_sell_price, action, market)
        return OrderProgram(mode, orders=_one("sell", price, qty, action))

    if mode == StrategyMode.COVER_DEFICIT:
        qty = valuation.deficit_kwh * max(0.0, action.qty_fraction)
        price = _effective_price("buy", valuation.fair_buy_price, action, market)
        return OrderProgram(mode, orders=_one("buy", price, qty, action))

    if mode == StrategyMode.BATTERY_ARBITRAGE:
        side = "sell" if valuation.soc_frac >= action.soc_target else "buy"
        base = valuation.battery_sell_price if side == "sell" else valuation.battery_buy_price
        qty = _battery_qty_kwh(ctx, valuation, action, side)
        price = _effective_price(side, base, action, market)
        return OrderProgram(mode, orders=_one(side, price, qty, action, dispatched=True))

    if mode == StrategyMode.AGGRESSIVE_TAKER:
        # Cross the spread on the imbalance side to take liquidity now.
        if valuation.surplus_kwh >= valuation.deficit_kwh:
            side, base, avail = "sell", valuation.fair_sell_price, valuation.surplus_kwh
            cross = market.best_bid
        else:
            side, base, avail = "buy", valuation.fair_buy_price, valuation.deficit_kwh
            cross = market.best_ask
        px = cross if cross is not None else Decimal(str(base))
        price = _effective_price(side, px, action, market)
        return OrderProgram(mode, orders=_one(side, price, avail * max(0.0, action.qty_fraction), action))

    if mode in (StrategyMode.LADDER_SELL, StrategyMode.LADDER_BUY):
        return _ladder(mode, action, valuation)

    if mode == StrategyMode.PASSIVE_MARKET_MAKE:
        return _market_make(action, ctx, valuation)

    if mode == StrategyMode.CANCEL_REPRICE:
        return _cancel_reprice(action, ctx, valuation)

    return OrderProgram(mode, orders=[])


def _one(side: str, price: Decimal, qty: float, action: StrategyAction, *, dispatched: bool = False) -> list[OrderSpec]:
    if qty <= 0:
        return []
    return [OrderSpec(side=side, price=price, qty=_q(qty), dispatched=dispatched, ttl_ticks=action.ttl_ticks)]


def _ladder(mode: StrategyMode, action: StrategyAction, valuation: ValuationSignal) -> OrderProgram:
    sell = mode == StrategyMode.LADDER_SELL
    avail = valuation.surplus_kwh if sell else valuation.deficit_kwh
    base = Decimal(str(valuation.fair_sell_price if sell else valuation.fair_buy_price))
    levels = max(1, action.ladder_levels)
    per = (avail * max(0.0, action.qty_fraction)) / levels
    slope = Decimal(str(action.ladder_slope))
    specs: list[OrderSpec] = []
    for i in range(levels):
        # Sells step up (ask higher further out); buys step down (bid lower).
        factor = (Decimal("1") + slope * i) if sell else (Decimal("1") - slope * i)
        price = (base * factor).quantize(QUANT)
        if price > 0 and per > 0:
            specs.append(OrderSpec("sell" if sell else "buy", price, _q(per), ttl_ticks=action.ttl_ticks))
    return OrderProgram(mode, orders=specs)


def _market_make(action: StrategyAction, ctx: AgentContext, valuation: ValuationSignal) -> OrderProgram:
    """Two-sided maker quotes straddling the battery fair value, backed by the
    battery (dispatched). Half-spread comes from ladder_slope (default a few %)."""
    mid = Decimal(str(valuation.marginal_battery_value))
    half = Decimal(str(action.ladder_slope)) if action.ladder_slope > 0 else Decimal("0.02")
    sell_qty = _battery_qty_kwh(ctx, valuation, action, "sell")
    buy_qty = _battery_qty_kwh(ctx, valuation, action, "buy")
    specs: list[OrderSpec] = []
    ask = (mid * (Decimal("1") + half)).quantize(QUANT)
    bid = (mid * (Decimal("1") - half)).quantize(QUANT)
    if sell_qty > 0:
        specs.append(OrderSpec("sell", ask, _q(sell_qty), dispatched=True, ttl_ticks=action.ttl_ticks))
    if buy_qty > 0:
        specs.append(OrderSpec("buy", bid, _q(buy_qty), dispatched=True, ttl_ticks=action.ttl_ticks))
    return OrderProgram(action.mode, orders=specs)


def _cancel_reprice(action: StrategyAction, ctx: AgentContext, valuation: ValuationSignal) -> OrderProgram:
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
            ttl_ticks=action.ttl_ticks,
        ),
        ctx,
        valuation,
    )
    policy = CancelPolicy(cancel_age_ticks=max(1, action.cancel_age_ticks), reprice=True)
    return OrderProgram(StrategyMode.CANCEL_REPRICE, orders=list(fresh.orders), cancel_policy=policy)
