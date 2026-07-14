"""Truthful valuation oracle.

The economic model formerly embedded in `TruthfulAgent.decide()`, extracted verbatim
so it can be shared. It estimates fair buy/sell prices, battery opportunity cost,
energy imbalance, and SOC pressure from first principles, *without* deciding how to
act on them.

Marginal cost / value model (unchanged from the Truthful agent):
- Pure renewable surplus has ~0 marginal cost → offer at `markup_floor * price_ref`.
- Battery discharge cost: `price_ref / sqrt(eta_rt)` (round-trip efficiency loss).
- Battery charge value: `price_ref * sqrt(eta_rt)`.
- Deficit: pay up to `price_ref` for direct load coverage, rising with scarcity when
  `demand_beta > 0` (capped at `price_ref * price_cap_mult`).

`price_ref`, `demand_beta`, and `price_cap_mult` are per-agent economic parameters
(`price_ref` is jittered per VPP for cost diversification). `markup_floor`, battery
efficiency, and capacity are read live from the `AgentContext`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import AgentContext
from eflux.agents.valuation.schema import ValuationSignal
from eflux.market.delivery import OrderPurpose


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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


@dataclass
class TruthfulValuationOracle:
    price_ref: Decimal = Decimal("50.0")
    # Price-responsive demand: bid rises with the deficit fraction up to
    # price_ref * price_cap_mult. 0.0 = flat bidding at price_ref (legacy).
    demand_beta: float = 0.0
    price_cap_mult: float = 1.5
    # Neutral SOC for the (informational) soc_pressure signal.
    soc_neutral: float = 0.5

    def reset(self) -> None:
        """Valuation is stateless; retained as the policy lifecycle hook."""

    def estimate(self, ctx: AgentContext) -> ValuationSignal:
        pr = float(self.price_ref)
        eta = max(0.01, ctx.battery.eta_rt)
        sqrt_eta = math.sqrt(eta)
        battery_sell_price = pr / sqrt_eta  # cost to deliver from battery
        battery_buy_price = pr * sqrt_eta  # value of storing to battery

        # Quote only the forced balance not already resting on the same side of
        # the book. This assumes a VPP is not intentionally resting both sides for
        # the same forced position; two-sided market making remains represented by
        # the separate scarcity term below.
        net_kwh = (
            ctx.projected_net_kwh
            if ctx.projected_net_kwh is not None
            else ctx.state.pending_net_kwh
        )
        effective_kwh = net_kwh - ctx.contracted_net_kwh - ctx.open_orders_net_kwh
        surplus_kwh = max(0.0, effective_kwh)
        deficit_kwh = max(0.0, -effective_kwh)

        # Sell-side marginal cost: surplus within current PV+wind output (load
        # fully covered) is pure renewable export → quote the floor; surplus
        # beyond renewable output would be sourced from the battery → delivery cost.
        if ctx.state.net_kw <= ctx.state.pv_kw + ctx.state.wind_kw:
            fair_sell_price = float(ctx.params.markup_floor) * pr
        else:
            fair_sell_price = battery_sell_price

        # Buy-side marginal value: pay up to price_ref to cover load, rising with
        # scarcity. Keep this separate from effective_kwh above: resting bids
        # still indicate unserved demand depth for the scarcity premium.
        unserved_kwh = max(0.0, -(net_kwh + ctx.open_orders_net_kwh))
        deficit_frac = min(1.0, unserved_kwh / max(ctx.params.battery_kwh, 1.0))
        fair_buy_price = pr * min(self.price_cap_mult, 1.0 + self.demand_beta * deficit_frac)

        external = ctx.market.external_market
        if external is not None and external.is_real_price and ctx.market.anchor_to_external:
            anchor = float(external.p2p_anchor_price)
            fair_buy_price = min(fair_buy_price, anchor)
            fair_sell_price = max(fair_sell_price, anchor)
            battery_buy_price = min(battery_buy_price, anchor)
            battery_sell_price = max(battery_sell_price, anchor)

        # A gas provider offers one full delivery interval of capacity. Quote
        # cadence belongs to the decision scheduler, never to physics tick count.
        supply_purpose = OrderPurpose.BALANCE
        gas_kw = float(ctx.params.gas_kw_max)
        if gas_kw > 0.0:
            surplus_kwh = gas_kw * ctx.primary_interval.duration_h
            fair_sell_price = float(ctx.params.gas_cost_per_mwh)
            supply_purpose = OrderPurpose.DISPATCHABLE

        soc_frac = ctx.battery.soc_frac
        expected_ref_1h: float | None = None
        expected_ref_12h: float | None = None
        price_trend = 0.0
        series = _forecast_price_series(ctx)
        if series is not None:
            try:
                expected_ref_1h = float(series.by_horizon("1h").value)
                expected_ref_12h = float(series.by_horizon("12h").value)
            except (AttributeError, KeyError, TypeError, ValueError):
                expected_ref_1h = None
                expected_ref_12h = None
            if expected_ref_1h is not None:
                current_ref = 0.5 * (battery_sell_price + battery_buy_price)
                eps = 1.0e-9
                price_trend = _clamp(
                    (expected_ref_1h - current_ref) / max(current_ref, eps),
                    -1.0,
                    1.0,
                )
        return ValuationSignal(
            fair_buy_price=fair_buy_price,
            fair_sell_price=fair_sell_price,
            marginal_battery_value=0.5 * (battery_sell_price + battery_buy_price),
            battery_sell_price=battery_sell_price,
            battery_buy_price=battery_buy_price,
            surplus_kwh=surplus_kwh,
            deficit_kwh=deficit_kwh,
            soc_frac=soc_frac,
            soc_pressure=soc_frac - self.soc_neutral,
            supply_purpose=supply_purpose,
            expected_ref_1h=expected_ref_1h,
            expected_ref_12h=expected_ref_12h,
            price_trend=price_trend,
        )
