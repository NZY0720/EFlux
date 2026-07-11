"""Strategy policy seam.

`StrategyPolicy.select_action` is the single decision point a tactical policy implements:
given the market/VPP context and the valuation signal (and, later, slow LLM guidance), it
chooses one `StrategyAction`. This is the seam every policy plugs into without touching the
agent, compiler, or risk gate:

- `ScriptedStrategyPolicy` (here) — a deterministic baseline mirroring Truthful's
  imbalance logic.
- `PPOPrimitiveAgent` (M4) — a learned policy over the same action space.
- LLM-guided policy (M6) — the scripted/PPO policy biased by `StrategyGuidance`.

Keeping the action space identical across all three is what lets PPO learn behaviour that
departs from Truthful while staying interpretable and risk-gated (design note §5.2, §8).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from eflux.agents.base import AgentContext
from eflux.agents.strategy.schema import (
    PRICE_MULT_MAX,
    PRICE_MULT_MIN,
    StrategyAction,
    StrategyMode,
)
from eflux.agents.valuation import ValuationSignal

if TYPE_CHECKING:
    from eflux.agents._baseline_common import BaselineAgent

GRID_PRICE_MARGIN = 0.05
WAIT_FOR_BETTER_MARGIN = 0.03


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _positive(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out > 0.0 else None


def _valuation_ref(valuation: ValuationSignal) -> float:
    ref = float(getattr(valuation, "marginal_battery_value", 0.0) or 0.0)
    if ref > 0.0:
        return ref
    return 0.5 * (
        float(getattr(valuation, "fair_buy_price", 0.0) or 0.0)
        + float(getattr(valuation, "fair_sell_price", 0.0) or 0.0)
    )


def _forecast_price_series(ctx: AgentContext):
    forecast = ctx.forecast
    # A never-refreshed bundle (model_version "empty") is all zeros; reading it as a
    # real price signal turns the trend hard negative and stalls trading. Treat it
    # as "no forecast" so agents fall back to neutral-trend behaviour.
    if forecast is None or getattr(forecast, "model_version", "") == "empty":
        return None
    if ctx.market.market_mode == "realprice":
        return getattr(forecast, "price_real", None)
    return getattr(forecast, "price_p2p", None) or getattr(forecast, "price_real", None)


def _price_trend(ctx: AgentContext, valuation: ValuationSignal) -> float:
    trend = float(getattr(valuation, "price_trend", 0.0) or 0.0)
    if trend != 0.0:
        return _clamp(trend, -1.0, 1.0)
    expected = getattr(valuation, "expected_ref_1h", None)
    if expected is None:
        series = _forecast_price_series(ctx)
        if series is None:
            return 0.0
        try:
            expected = float(series.by_horizon("1h").value)
        except (AttributeError, KeyError, TypeError, ValueError):
            return 0.0
    ref = _valuation_ref(valuation)
    if ref <= 0.0:
        return 0.0
    return _clamp((float(expected) - ref) / max(ref, 1.0e-9), -1.0, 1.0)


def _market_mid(ctx: AgentContext) -> float | None:
    return _positive(ctx.market.mid_price) or _positive(ctx.market.last_price)


def _forecast_reference(ctx: AgentContext, valuation: ValuationSignal) -> float | None:
    return (
        _positive(getattr(valuation, "expected_ref_12h", None))
        or _positive(getattr(valuation, "expected_ref_1h", None))
        or _positive(getattr(valuation, "marginal_battery_value", None))
        or _market_mid(ctx)
        or _positive(
            0.5
            * (
                float(getattr(valuation, "fair_buy_price", 0.0) or 0.0)
                + float(getattr(valuation, "fair_sell_price", 0.0) or 0.0)
            )
        )
    )


def _current_grid_buy_price(ctx: AgentContext, valuation: ValuationSignal) -> float | None:
    quote = ctx.market.external_market
    return (
        _positive(getattr(quote, "import_price", None))
        or _positive(getattr(valuation, "fair_buy_price", None))
        or _market_mid(ctx)
    )


def _current_grid_sell_price(ctx: AgentContext, valuation: ValuationSignal) -> float | None:
    quote = ctx.market.external_market
    return (
        _positive(getattr(quote, "export_price", None))
        or _positive(getattr(valuation, "fair_sell_price", None))
        or _market_mid(ctx)
    )


def _tilt_soc_target(
    action: StrategyAction,
    ctx: AgentContext,
    valuation: ValuationSignal,
    *,
    enabled: bool,
) -> StrategyAction:
    if not enabled:
        return action
    trend = _price_trend(ctx, valuation)
    if trend == 0.0:
        return action
    soc_target = _clamp(float(action.soc_target) + 0.15 * trend, 0.0, 1.0)
    return replace(action, soc_target=soc_target)


def _tilt_price_mult(
    mult: float,
    *,
    side: str,
    ctx: AgentContext,
    valuation: ValuationSignal,
    enabled: bool,
) -> float:
    if not enabled:
        return mult
    trend = _price_trend(ctx, valuation)
    if trend == 0.0:
        return mult
    tilted = mult * (1.0 + 0.08 * trend)
    if side == "sell":
        tilted = max(1.0, tilted)
    else:
        tilted = min(1.0, tilted)
    return _clamp(tilted, PRICE_MULT_MIN, PRICE_MULT_MAX)


@runtime_checkable
class StrategyPolicy(Protocol):
    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        ...


@dataclass
class ScriptedStrategyPolicy:
    """Deterministic baseline policy: trade the ambient imbalance, else stand down.

    Mirrors the Truthful agent's primary behaviour within the one-action-per-tick
    contract that PPO will inherit — surplus → liquidate, deficit → cover, balanced →
    no-op. Battery-band arbitrage is left to the learned policy (the primitive exists
    and is gated; sizing it well across cadences is exactly what PPO should learn)."""

    min_qty: float = 0.01
    use_forecast: bool = False

    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        if valuation.surplus_kwh >= self.min_qty:
            return _tilt_soc_target(
                self._guided(
                    StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS),
                    "sell",
                    guidance,
                ),
                ctx,
                valuation,
                enabled=self.use_forecast,
            )
        if valuation.deficit_kwh >= self.min_qty:
            return _tilt_soc_target(
                self._guided(
                    StrategyAction(mode=StrategyMode.COVER_DEFICIT),
                    "buy",
                    guidance,
                ),
                ctx,
                valuation,
                enabled=self.use_forecast,
            )
        return _tilt_soc_target(
            self._guided(StrategyAction(mode=StrategyMode.NOOP), "neutral", guidance),
            ctx,
            valuation,
            enabled=self.use_forecast,
        )

    def _guided(
        self,
        base: StrategyAction,
        side: str,
        guidance: object | None,
    ) -> StrategyAction:
        """Adopt a preferred primitive only when it is compatible with the current
        imbalance. The guidance remains soft: incompatible preferences are ignored,
        and avoided base modes are merely shrunk later by apply_guidance()."""
        preferred = tuple(getattr(guidance, "preferred_modes", ()) or ())
        if not preferred:
            return base
        compatible = _COMPATIBLE_MODES[side]
        for mode in preferred:
            if mode in compatible:
                return _action_for_preferred(mode)
        return base


_COMPATIBLE_MODES: dict[str, set[StrategyMode]] = {
    "sell": {
        StrategyMode.LIQUIDATE_SURPLUS,
        StrategyMode.LADDER_SELL,
        StrategyMode.AGGRESSIVE_TAKER,
        StrategyMode.PASSIVE_MARKET_MAKE,
        StrategyMode.BATTERY_ARBITRAGE,
    },
    "buy": {
        StrategyMode.COVER_DEFICIT,
        StrategyMode.LADDER_BUY,
        StrategyMode.AGGRESSIVE_TAKER,
        StrategyMode.PASSIVE_MARKET_MAKE,
        StrategyMode.BATTERY_ARBITRAGE,
    },
    "neutral": {
        StrategyMode.NOOP,
        StrategyMode.HOLD_ENERGY,
        StrategyMode.PASSIVE_MARKET_MAKE,
        StrategyMode.BATTERY_ARBITRAGE,
    },
}


def _action_for_preferred(mode: StrategyMode) -> StrategyAction:
    if mode in (StrategyMode.LADDER_SELL, StrategyMode.LADDER_BUY):
        return StrategyAction(mode=mode, ladder_levels=3, ladder_slope=0.01)
    if mode == StrategyMode.AGGRESSIVE_TAKER:
        return StrategyAction(mode=mode, aggressiveness=1.0)
    if mode == StrategyMode.PASSIVE_MARKET_MAKE:
        return StrategyAction(mode=mode, qty_fraction=0.5, ladder_slope=0.02)
    return StrategyAction(mode=mode)


@dataclass
class BatteryAwareStrategyPolicy:
    """Deterministic PPO-space demonstrator for the battery-buffer environment.

    It first clears forced imbalance, then uses the battery primitive only when price
    is on the right side of the oracle's battery value and SOC has room inside the
    demonstrator guard band. This keeps BC targets in the four PPO modes while avoiding
    invalid battery actions at the SOC extremes.
    """

    min_qty: float = 0.01
    battery_buy_price_mult: float = 1.2
    battery_sell_price_mult: float = 1.0
    battery_aggressiveness: float = 1.0
    use_forecast: bool = False
    spread_margin: float = 0.05
    arb_soc_high: float = 0.9
    arb_soc_low: float = 0.2

    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        if ctx.market.market_mode == "realprice":
            grid_action = self._select_realprice_action(ctx, valuation)
            if grid_action is not None:
                return grid_action

        min_qty = max(0.0, float(self.min_qty))
        surplus = max(0.0, float(valuation.surplus_kwh or 0.0))
        deficit = max(0.0, float(valuation.deficit_kwh or 0.0))
        soc = max(0.0, min(1.0, float(valuation.soc_frac or 0.0)))
        last = self._last_price(ctx)

        if deficit >= min_qty:
            return _tilt_soc_target(
                StrategyAction(mode=StrategyMode.COVER_DEFICIT),
                ctx,
                valuation,
                enabled=self.use_forecast,
            )

        if surplus >= min_qty:
            fair_sell = float(valuation.fair_sell_price or 0.0)
            if last is None or last >= fair_sell:
                return _tilt_soc_target(
                    StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS),
                    ctx,
                    valuation,
                    enabled=self.use_forecast,
                )
            return _tilt_soc_target(
                StrategyAction(mode=StrategyMode.NOOP),
                ctx,
                valuation,
                enabled=self.use_forecast,
            )

        if last is not None:
            expected_1h = getattr(valuation, "expected_ref_1h", None)
            if self.use_forecast and expected_1h is not None:
                expected_1h = float(expected_1h)
                spread_margin = max(0.0, float(self.spread_margin))
                arb_soc_high = max(0.0, min(1.0, float(self.arb_soc_high)))
                arb_soc_low = max(0.0, min(1.0, float(self.arb_soc_low)))
                battery_buy = float(valuation.battery_buy_price or 0.0)
                battery_sell = float(valuation.battery_sell_price or float("inf"))
                # battery_buy/battery_sell = pr·√η ÷ pr/√η = η, so the cross-terms
                # below require the spread to clear the round-trip efficiency loss.
                if (
                    expected_1h >= last * (1.0 + spread_margin)
                    and expected_1h * battery_buy > last * battery_sell
                    and soc < arb_soc_high
                ):
                    return _tilt_soc_target(
                        StrategyAction(
                            mode=StrategyMode.BATTERY_ARBITRAGE,
                            aggressiveness=self.battery_aggressiveness,
                            soc_target=arb_soc_high,
                        ),
                        ctx,
                        valuation,
                        enabled=self.use_forecast,
                    )
                if (
                    expected_1h <= last * (1.0 - spread_margin)
                    and last * battery_buy > expected_1h * battery_sell
                    and soc > arb_soc_low
                ):
                    return _tilt_soc_target(
                        StrategyAction(
                            mode=StrategyMode.BATTERY_ARBITRAGE,
                            aggressiveness=self.battery_aggressiveness,
                            soc_target=arb_soc_low,
                        ),
                        ctx,
                        valuation,
                        enabled=self.use_forecast,
                    )

            battery_buy = float(valuation.battery_buy_price or 0.0)
            battery_sell = float(valuation.battery_sell_price or 0.0)
            if battery_buy > 0.0 and last <= battery_buy * self.battery_buy_price_mult and soc < 0.9:
                return _tilt_soc_target(
                    StrategyAction(
                        mode=StrategyMode.BATTERY_ARBITRAGE,
                        aggressiveness=self.battery_aggressiveness,
                        soc_target=0.9,
                    ),
                    ctx,
                    valuation,
                    enabled=self.use_forecast,
                )
            if battery_sell > 0.0 and last >= battery_sell * self.battery_sell_price_mult and soc > 0.2:
                return _tilt_soc_target(
                    StrategyAction(
                        mode=StrategyMode.BATTERY_ARBITRAGE,
                        aggressiveness=self.battery_aggressiveness,
                        soc_target=0.2,
                    ),
                    ctx,
                    valuation,
                    enabled=self.use_forecast,
                )

        return _tilt_soc_target(
            StrategyAction(mode=StrategyMode.NOOP),
            ctx,
            valuation,
            enabled=self.use_forecast,
        )

    def _last_price(self, ctx: AgentContext) -> float | None:
        if ctx.market.last_price is not None:
            return float(ctx.market.last_price)
        if ctx.market.mid_price is not None:
            return float(ctx.market.mid_price)
        return None

    def _select_realprice_action(
        self, ctx: AgentContext, valuation: ValuationSignal
    ) -> StrategyAction | None:
        min_qty = max(0.0, float(self.min_qty))
        surplus = max(0.0, float(valuation.surplus_kwh or 0.0))
        deficit = max(0.0, float(valuation.deficit_kwh or 0.0))
        soc = max(0.0, min(1.0, float(valuation.soc_frac or 0.0)))
        trend = _price_trend(ctx, valuation)
        reference = _forecast_reference(ctx, valuation)
        buy_price = _current_grid_buy_price(ctx, valuation)
        sell_price = _current_grid_sell_price(ctx, valuation)

        if surplus >= min_qty and trend > WAIT_FOR_BETTER_MARGIN and soc < 0.95:
            return StrategyAction(mode=StrategyMode.WAIT_FOR_BETTER)
        if deficit >= min_qty and trend < -WAIT_FOR_BETTER_MARGIN and soc > 0.05:
            return StrategyAction(mode=StrategyMode.WAIT_FOR_BETTER)

        if reference is not None:
            if (
                buy_price is not None
                and buy_price < reference * (1.0 - GRID_PRICE_MARGIN)
                and trend > 0.0
                and soc < 0.9
            ):
                return StrategyAction(
                    mode=StrategyMode.GRID_CHARGE_ON_DIP,
                    aggressiveness=self.battery_aggressiveness,
                    soc_target=0.9,
                )
            if (
                sell_price is not None
                and sell_price > reference * (1.0 + GRID_PRICE_MARGIN)
                and soc > 0.2
            ):
                return StrategyAction(
                    mode=StrategyMode.GRID_DISCHARGE_ON_PEAK,
                    aggressiveness=self.battery_aggressiveness,
                    soc_target=0.2,
                )

        if deficit >= min_qty:
            return _tilt_soc_target(
                StrategyAction(mode=StrategyMode.COVER_DEFICIT),
                ctx,
                valuation,
                enabled=self.use_forecast,
            )

        if surplus >= min_qty:
            return _tilt_soc_target(
                StrategyAction(mode=StrategyMode.LIQUIDATE_SURPLUS),
                ctx,
                valuation,
                enabled=self.use_forecast,
            )

        battery_buy = float(valuation.battery_buy_price or 0.0)
        battery_sell = float(valuation.battery_sell_price or 0.0)
        if buy_price is not None and battery_buy > 0.0 and buy_price <= battery_buy * self.battery_buy_price_mult and soc < 0.9:
            return StrategyAction(
                mode=StrategyMode.GRID_CHARGE_ON_DIP,
                aggressiveness=self.battery_aggressiveness,
                soc_target=0.9,
            )
        if sell_price is not None and battery_sell > 0.0 and sell_price >= battery_sell * self.battery_sell_price_mult and soc > 0.2:
            return StrategyAction(
                mode=StrategyMode.GRID_DISCHARGE_ON_PEAK,
                aggressiveness=self.battery_aggressiveness,
                soc_target=0.2,
            )

        return StrategyAction(mode=StrategyMode.NOOP)


@dataclass
class BaselinePolicy:
    """Adapt a classical CDA baseline (AA / ZIP / GD / Truthful) into the `StrategyPolicy`
    seam so the slow LLM strategist can coach it *exactly* like the PPO executor.

    The wrapped baseline computes its own target quote price via its `_quote_price` rule
    (AA's p*/aggressiveness, ZIP's adaptive margin, GD's belief maximiser); we express that
    price as a `StrategyAction` over the shared action space via `price_target_mult`. With no
    guidance the neutral action reproduces the standalone baseline byte-for-byte (the compiler's
    `_anchor` recovers `limit * (target/limit) == target`); when guidance arrives,
    `apply_guidance` layers the LLM's binding mode_pin + soft risk/price/soc bias on top. The
    oracle → compiler → TradingGatewayV2 pipeline and the baseline's own rationality clamp
    are reused unchanged. Fills are forwarded so the baseline's online adaptation keeps learning.
    """

    base: BaselineAgent
    use_forecast: bool = False

    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        min_qty = float(self.base.min_qty)
        if valuation.surplus_kwh >= min_qty:
            side, limit, mode = "sell", valuation.fair_sell_price, StrategyMode.LIQUIDATE_SURPLUS
        elif valuation.deficit_kwh >= min_qty:
            side, limit, mode = "buy", valuation.fair_buy_price, StrategyMode.COVER_DEFICIT
        else:
            return StrategyAction(mode=StrategyMode.NOOP)
        anchor = float(limit)
        if anchor <= 0:
            return StrategyAction(mode=mode)
        target = self.base._quote_price(side=side, limit=anchor, ctx=ctx, sig=valuation)
        # Reuse the baseline's individual-rationality clamp: a seller never prices below its
        # marginal cost (mult ≥ 1), a buyer never above its marginal value (mult ≤ 1).
        target = self.base._rationalize(side, target, anchor)
        mult = max(PRICE_MULT_MIN, min(PRICE_MULT_MAX, target / anchor))
        mult = _tilt_price_mult(
            mult,
            side=side,
            ctx=ctx,
            valuation=valuation,
            enabled=self.use_forecast,
        )
        return StrategyAction(mode=mode, price_target_mult=mult)

    def record_trade(self, record: dict) -> None:
        self.base.record_trade(record)
