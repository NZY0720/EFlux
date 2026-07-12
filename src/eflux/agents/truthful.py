"""Truthful (cost-based) agent.

Reports its true marginal cost (sell side) or marginal value (buy side) every tick.
No random noise, no strategic shading. Useful as a baseline against ZI/PPO to verify
that smarter strategies actually improve PnL.

The economic model lives in `TruthfulValuationOracle` (agents/valuation): this agent
is now a thin assembler that reads the oracle's `ValuationSignal` and turns it into
orders — the accumulated-balance quote plus a throttled battery-band arbitrage quote.
Demoting the valuation to a shared oracle lets the compiler and TradingGatewayV2 read
the same numbers instead of re-deriving them.

Side choice mirrors ZI: positive net energy ⇒ sell; negative ⇒ buy; balanced ⇒ no order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from eflux.agents.base import AgentContext, BaseAgent
from eflux.agents.decision import AgentDecision, OrderRequest
from eflux.agents.valuation import TruthfulValuationOracle, ValuationSignal
from eflux.market.delivery import OrderPurpose


@dataclass
class TruthfulAgent(BaseAgent):
    price_ref: Decimal = Decimal("50.0")
    min_qty: Decimal = Decimal("0.01")
    # Battery arbitrage band. Above soc_high the agent offers stored energy at
    # its delivery cost; below soc_low it bids to recharge at its storage
    # value. Without this, nighttime (PV=0) leaves every VPP in deficit — a
    # market with only buyers and zero trades. The band straddles the 50%
    # boot SOC so a fresh market has supply from the first minute.
    soc_high: float = 0.45
    soc_low: float = 0.25
    battery_quote_every_n_decisions: int = 1
    # Price-responsive demand: bid price rises with the deficit fraction
    # (deficit relative to battery capacity), up to price_ref * price_cap_mult.
    # 0.0 = legacy flat bidding at price_ref. With demand_beta=0.5 an urgent
    # (full-capacity) deficit bids 75 — crossing the entire gas merit order
    # (55-72) so peaking units actually clear at scarcity hours.
    demand_beta: float = 0.0
    price_cap_mult: float = 1.5
    _ticks_since_battery_quote: int = 0

    def __post_init__(self) -> None:
        # The agent owns its valuation oracle, configured from the same economic
        # parameters (price_ref is jittered per VPP for cost diversification).
        self._oracle = TruthfulValuationOracle(
            price_ref=self.price_ref,
            demand_beta=self.demand_beta,
            price_cap_mult=self.price_cap_mult,
        )

    def decide(self, ctx: AgentContext) -> AgentDecision:
        sig = self._oracle.estimate(ctx)
        requests: list[OrderRequest] = []
        min_qty_f = float(self.min_qty)

        # 1) Quote the accumulated untraded balance, not this tick's sliver of
        # energy: with a 1s tick the per-tick net is ~1e-3 kWh and would never
        # clear min_qty. The oracle reports it as surplus_kwh / deficit_kwh.
        if sig.surplus_kwh >= min_qty_f:
            # Gas-backed surplus settles through fuel, not ambient balance.
            self._append_balance_order(
                requests,
                ctx,
                "sell",
                sig.fair_sell_price,
                sig.surplus_kwh,
                purpose=sig.supply_purpose,
            )
        elif sig.deficit_kwh >= min_qty_f:
            self._append_balance_order(
                requests,
                ctx,
                "buy",
                sig.fair_buy_price,
                sig.deficit_kwh,
                purpose=OrderPurpose.BALANCE,
            )

        # 2) Battery-band arbitrage quote (throttled). Sized to what the battery
        # could physically deliver over the cooldown window, capped by the SOC
        # distance to the band edge so it self-limits as fills move the SOC.
        self._append_battery_band_order(requests, ctx, sig)

        return AgentDecision(orders=tuple(requests))

    def _quote_price(
        self, *, side: str, limit: float, ctx: AgentContext, sig: ValuationSignal
    ) -> float:
        """BaselinePolicy adapter: truthful quoting is exactly the fair-value limit."""
        return limit

    @staticmethod
    def _rationalize(side: str, price_f: float, limit: float) -> float:
        """Match the classical adapter's individual-rationality clamp."""
        return max(price_f, limit) if side == "sell" else min(price_f, limit)

    def _append_balance_order(
        self,
        requests: list[OrderRequest],
        ctx: AgentContext,
        side: str,
        price_f: float,
        qty_f: float,
        *,
        purpose: OrderPurpose,
    ) -> None:
        price = Decimal(str(price_f)).quantize(Decimal("0.0001"))
        qty = Decimal(str(qty_f)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        if price.is_finite() and qty >= self.min_qty:
            requests.append(
                OrderRequest(
                    side=side,
                    price=price,
                    qty_kwh=qty,
                    interval=ctx.primary_interval,
                    purpose=purpose,
                    ttl_sec=ctx.decision_interval_sec,
                )
            )

    def _append_battery_band_order(
        self, requests: list[OrderRequest], ctx: AgentContext, sig: ValuationSignal
    ) -> None:
        self._ticks_since_battery_quote += 1
        if self._ticks_since_battery_quote < self.battery_quote_every_n_decisions:
            return
        block = ctx.battery.max_power_kw * ctx.primary_interval.duration_h
        soc = ctx.battery.soc_frac
        cap = ctx.battery.capacity_kwh
        eta = math.sqrt(ctx.battery.eta_rt)
        batt_side: str | None = None
        if soc > self.soc_high:
            batt_side = "sell"
            batt_qty = min(block, (soc - self.soc_high) * cap * eta)
            batt_price = sig.battery_sell_price
        elif soc < self.soc_low:
            batt_side = "buy"
            batt_qty = min(block, (self.soc_low - soc) * cap / eta)
            batt_price = sig.battery_buy_price
        if batt_side is not None and batt_qty >= float(self.min_qty):
            self._ticks_since_battery_quote = 0
            requests.append(
                OrderRequest(
                    side=batt_side,
                    price=Decimal(str(batt_price)).quantize(Decimal("0.0001")),
                    qty_kwh=Decimal(str(batt_qty)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN),
                    interval=ctx.primary_interval,
                    purpose=OrderPurpose.BATTERY,
                    ttl_sec=ctx.decision_interval_sec,
                )
            )
