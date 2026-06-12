"""ReflectiveAgent — wraps a baseline agent with periodic LLM-driven hints.

Learning loop: every reflection closes the previous hints' outcome window
(PnL/trade delta since they were issued), persists it to AgentMemory, and feeds
the last few outcomes — plus market-wide trades and the other LLM agents'
latest views — back into the next prompt. Several agents share one slow LLM
endpoint, so reflections are offset per agent and serialized by a shared gate.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from eflux.agents.base import AgentContext, BaseAgent, OrderIntent
from eflux.agents.reflective.llm_client import LLMClient
from eflux.agents.reflective.memory import AgentMemory
from eflux.agents.reflective.prompt import (
    ReflectionHints,
    build_system_prompt,
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
    # Hard ceiling on a reflection round-trip. The httpx client has its own
    # timeout, but a wedged connection pool would otherwise leave
    # _reflection_in_flight True forever and silently stop all reflections.
    hard_timeout_sec: float = 180.0
    # Stagger: this agent reflects on ticks where
    # tick % interval == reflect_offset_ticks % interval. The loader spreads
    # offsets evenly so co-located agents don't hit the endpoint together.
    reflect_offset_ticks: int = 0
    # Shared across all reflective agents: at most one in-flight LLM call.
    # When another agent holds it at trigger time, this cycle is skipped
    # (counted, not failed) instead of queueing against a slow endpoint.
    llm_gate: asyncio.Semaphore | None = None
    # Strategy brief appended to the system prompt (from AgentSpec.persona).
    persona_prompt: str | None = None
    # Hint→outcome records; persistent when constructed with a path.
    memory: AgentMemory = field(default_factory=lambda: AgentMemory(None))

    _hints: ReflectionHints = field(default_factory=ReflectionHints)
    _tick_count: int = 0
    _recent_pnl: list[float] = field(default_factory=list)
    _recent_trades: list[dict] = field(default_factory=list)
    _reflection_in_flight: bool = False
    _last_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    _trade_count: int = 0
    # Open outcome window: trigger-time state captured when the current hints
    # landed; closed (→ memory record) at the next reflection trigger.
    _marker: dict | None = None
    # Strong reference to the in-flight reflection task — the event loop only
    # keeps weak refs, so a fire-and-forget task could be garbage-collected
    # mid-call without this.
    _reflection_task: asyncio.Task | None = None

    # Reflection audit trail — every LLM round-trip (success or failure) lands
    # here so the API/UI can show what the agent was thinking and how healthy
    # the LLM link is. Entries: {ts, ok, price_adjust, qty_scale, rationale, lesson, error}.
    reflection_log: deque = field(default_factory=lambda: deque(maxlen=50))
    ok_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
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
            and self._tick_count % self.reflect_every_n_ticks
            == self.reflect_offset_ticks % self.reflect_every_n_ticks
        ):
            if self.llm_gate is not None and self.llm_gate.locked():
                # Another agent's call is in flight against the shared (slow)
                # endpoint — skip this cycle rather than pile on.
                self.skipped_count += 1
            else:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    # No event loop (e.g. called from a sync test). Skip silently.
                    pass
                else:
                    # Snapshot trigger-time state BY VALUE: ctx.state is a live
                    # object mutated every tick while the LLM call is in flight.
                    trigger = {
                        "pnl": float(ctx.state.pnl),
                        "tick": self._tick_count,
                        "trades": self._trade_count,
                        "sim_ts": ctx.state.sim_ts.isoformat(),
                        "soc": float(ctx.battery.soc_frac),
                    }
                    peer_views = [
                        {k: v for k, v in p.items() if k != "vpp_id"}
                        for p in ctx.market.peer_reflections
                        if p.get("vpp_id") != ctx.vpp_id
                    ]
                    # Flag only after the task exists: setting it first meant any
                    # create_task failure left it stuck True, permanently disabling
                    # reflection for the rest of the run.
                    self._reflection_task = loop.create_task(
                        self._refresh_hints(
                            ctx,
                            trigger=trigger,
                            market_trades=list(ctx.market.recent_trades),
                            peer_views=peer_views,
                        )
                    )
                    self._reflection_in_flight = True

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
            adjusted.append(
                OrderIntent(side=it.side, price=adj_price, qty=adj_qty, dispatched=it.dispatched)
            )
        return adjusted

    def _apply_price_hint(self, intent: OrderIntent) -> Decimal:
        # Buyers benefit from a positive price_adjust (bid higher to win);
        # sellers benefit from a *negative* price_adjust applied to ask (ask lower to win).
        sign = Decimal("1") if intent.side == "buy" else Decimal("-1")
        adj = Decimal("1") + sign * Decimal(str(self._hints.price_adjust))
        return (intent.price * adj).quantize(Decimal("0.0001"))

    def record_trade(self, trade: dict) -> None:
        self._trade_count += 1
        self._recent_trades.append(trade)
        if len(self._recent_trades) > self.history_size:
            self._recent_trades = self._recent_trades[-self.history_size :]

    def _close_outcome_window(self, trigger: dict) -> None:
        """Attribute the previous hints to the window that followed them and
        persist the record — this is what the agent learns from."""
        marker = self._marker
        if marker is None:
            return
        self._marker = None
        record = {
            "v": 1,
            "ts": datetime.now(UTC).isoformat(),
            "sim_ts": trigger["sim_ts"],
            "tick": trigger["tick"],
            "hints": marker["hints"],
            "rationale": marker["rationale"],
            "lesson": marker["lesson"],
            "window": {
                "ticks": trigger["tick"] - marker["tick"],
                "pnl": round(trigger["pnl"] - marker["pnl"], 4),
                "trades": trigger["trades"] - marker["trades"],
                "soc_end": round(trigger["soc"], 3),
            },
        }
        self.memory.append(record)

    async def _refresh_hints(
        self,
        ctx: AgentContext,
        *,
        trigger: dict | None = None,
        market_trades: list[dict] | None = None,
        peer_views: list[dict] | None = None,
    ) -> None:
        try:
            if self.llm_gate is not None:
                # Re-check here, not just in decide(): two agents triggering on
                # the same tick both see an unlocked gate at decide() time (the
                # tasks haven't run yet) — the loser must skip, not queue for
                # up to hard_timeout_sec against the slow endpoint. Tasks start
                # in creation order and the winner holds the gate before its
                # first real await, so this check is reliable.
                if self.llm_gate.locked():
                    self.skipped_count += 1
                    return
                async with self.llm_gate:
                    await self._reflect_once(ctx, trigger, market_trades, peer_views)
            else:
                await self._reflect_once(ctx, trigger, market_trades, peer_views)
        finally:
            self._reflection_in_flight = False

    async def _reflect_once(
        self,
        ctx: AgentContext,
        trigger: dict | None,
        market_trades: list[dict] | None,
        peer_views: list[dict] | None,
    ) -> None:
        try:
            # Close the previous hints' window first so the prompt below can
            # include the freshest outcome. Off-thread: the JSONL flush is
            # blocking file I/O and this coroutine runs on the event loop
            # (holding the LLM gate) — a slow disk must not stall every tick.
            if trigger is not None:
                await asyncio.to_thread(self._close_outcome_window, trigger)
            user_msg = build_user_message(
                recent_pnl=self._recent_pnl,
                recent_trades=self._recent_trades,
                soc_frac=ctx.battery.soc_frac,
                best_bid=float(ctx.market.best_bid) if ctx.market.best_bid is not None else None,
                best_ask=float(ctx.market.best_ask) if ctx.market.best_ask is not None else None,
                last_price=float(ctx.market.last_price)
                if ctx.market.last_price is not None
                else None,
                past_hint_outcomes=self.memory.last(5),
                market_trades=market_trades,
                peer_views=peer_views,
            )
            content = await asyncio.wait_for(
                self.llm_client.chat(  # type: ignore[union-attr]
                    [
                        {"role": "system", "content": build_system_prompt(self.persona_prompt)},
                        {"role": "user", "content": user_msg},
                    ]
                ),
                timeout=self.hard_timeout_sec,
            )
            if not content.strip():
                # Reasoning models return empty content when the token budget
                # is spent thinking — record as a failure, keep previous hints.
                raise RuntimeError("empty LLM response (completion budget exhausted?)")
            self._hints = parse_hints(content)
            self.ok_count += 1
            self.last_ok_ts = datetime.now(UTC)
            if trigger is not None:
                # Open the new outcome window from *now* (response time), not
                # the trigger snapshot: the LLM call may take many ticks,
                # during which the OLD hints were still steering — counting
                # that stretch against the new hints would misattribute it.
                self._marker = {
                    "hints": {
                        "price_adjust": self._hints.price_adjust,
                        "qty_scale": self._hints.qty_scale,
                    },
                    "rationale": self._hints.rationale,
                    "lesson": self._hints.lesson,
                    "pnl": float(self._last_pnl),
                    "tick": self._tick_count,
                    "trades": self._trade_count,
                }
            self.reflection_log.append(
                {
                    "ts": self.last_ok_ts,
                    "ok": True,
                    "price_adjust": self._hints.price_adjust,
                    "qty_scale": self._hints.qty_scale,
                    "rationale": self._hints.rationale,
                    "lesson": self._hints.lesson,
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
                    "lesson": "",
                    "error": f"{type(e).__name__}: {e}"[:200],
                }
            )
            log.exception("Reflection LLM call failed — keeping previous hints")
