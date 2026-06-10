"""ReflectiveAgent — wraps a baseline agent with periodic LLM-driven hints."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, BaseAgent, OrderIntent
from eflux.agents.reflective.llm_client import LLMClient
from eflux.agents.reflective.prompt import (
    SYSTEM_PROMPT,
    ReflectionHints,
    build_user_message,
    parse_hints,
)
from eflux.agents.truthful import TruthfulAgent

log = logging.getLogger(__name__)


@dataclass
class ReflectiveAgent(BaseAgent):
    """Decorator agent: every N ticks, refresh strategy hints from the LLM."""

    llm_client: LLMClient | None
    inner: BaseAgent = field(default_factory=TruthfulAgent)
    reflect_every_n_ticks: int = 60
    history_size: int = 20

    _hints: ReflectionHints = field(default_factory=ReflectionHints)
    _tick_count: int = 0
    _recent_pnl: list[float] = field(default_factory=list)
    _recent_trades: list[dict] = field(default_factory=list)
    _reflection_in_flight: bool = False
    _last_pnl: Decimal = field(default_factory=lambda: Decimal("0"))

    # Reflection audit trail — every LLM round-trip (success or failure) lands
    # here so the API/UI can show what the agent was thinking and how healthy
    # the LLM link is. Entries: {ts, ok, price_adjust, qty_scale, rationale, error}.
    reflection_log: deque = field(default_factory=lambda: deque(maxlen=50))
    ok_count: int = 0
    fail_count: int = 0
    last_ok_ts: datetime | None = None

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        # Track PnL delta vs last tick.
        pnl_delta = float(ctx.state.pnl - self._last_pnl)
        self._recent_pnl.append(pnl_delta)
        self._last_pnl = ctx.state.pnl
        # Cap history.
        if len(self._recent_pnl) > self.history_size:
            self._recent_pnl = self._recent_pnl[-self.history_size :]

        self._tick_count += 1
        if (
            self.llm_client is not None
            and not self._reflection_in_flight
            and self._tick_count % self.reflect_every_n_ticks == 0
        ):
            try:
                loop = asyncio.get_running_loop()
                self._reflection_in_flight = True
                loop.create_task(self._refresh_hints(ctx))
            except RuntimeError:
                # No event loop (e.g. called from a sync test). Skip silently.
                pass

        # Get baseline intents, then scale by hints.
        intents = self.inner.decide(ctx)
        if not intents:
            return []
        adjusted: list[OrderIntent] = []
        for it in intents:
            adj_price = self._apply_price_hint(it)
            adj_qty = (Decimal(str(self._hints.qty_scale)) * it.qty).quantize(Decimal("0.0001"))
            if adj_price <= 0 or adj_qty <= 0:
                continue
            adjusted.append(OrderIntent(side=it.side, price=adj_price, qty=adj_qty))
        return adjusted

    def _apply_price_hint(self, intent: OrderIntent) -> Decimal:
        # Buyers benefit from a positive price_adjust (bid higher to win);
        # sellers benefit from a *negative* price_adjust applied to ask (ask lower to win).
        sign = Decimal("1") if intent.side == "buy" else Decimal("-1")
        adj = Decimal("1") + sign * Decimal(str(self._hints.price_adjust))
        return (intent.price * adj).quantize(Decimal("0.0001"))

    def record_trade(self, trade: dict) -> None:
        self._recent_trades.append(trade)
        if len(self._recent_trades) > self.history_size:
            self._recent_trades = self._recent_trades[-self.history_size :]

    async def _refresh_hints(self, ctx: AgentContext) -> None:
        try:
            user_msg = build_user_message(
                recent_pnl=self._recent_pnl,
                recent_trades=self._recent_trades,
                soc_frac=ctx.battery.soc_frac,
                best_bid=float(ctx.market.best_bid) if ctx.market.best_bid is not None else None,
                best_ask=float(ctx.market.best_ask) if ctx.market.best_ask is not None else None,
                last_price=float(ctx.market.last_price) if ctx.market.last_price is not None else None,
            )
            content = await self.llm_client.chat(  # type: ignore[union-attr]
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ]
            )
            if not content.strip():
                # Reasoning models return empty content when the token budget
                # is spent thinking — record as a failure, keep previous hints.
                raise RuntimeError("empty LLM response (completion budget exhausted?)")
            self._hints = parse_hints(content)
            self.ok_count += 1
            self.last_ok_ts = datetime.now(UTC)
            self.reflection_log.append(
                {
                    "ts": self.last_ok_ts,
                    "ok": True,
                    "price_adjust": self._hints.price_adjust,
                    "qty_scale": self._hints.qty_scale,
                    "rationale": self._hints.rationale,
                    "error": None,
                }
            )
            log.info(
                "Reflective hint updated: price_adjust=%.3f qty_scale=%.3f (%s)",
                self._hints.price_adjust,
                self._hints.qty_scale,
                self._hints.rationale[:80],
            )
        except Exception as e:
            self.fail_count += 1
            self.reflection_log.append(
                {
                    "ts": datetime.now(UTC),
                    "ok": False,
                    "price_adjust": self._hints.price_adjust,
                    "qty_scale": self._hints.qty_scale,
                    "rationale": "",
                    "error": f"{type(e).__name__}: {e}"[:200],
                }
            )
            log.exception("Reflection LLM call failed — keeping previous hints")
        finally:
            self._reflection_in_flight = False
