"""StrategyAgent — the structured-language agent.

Assembles the M1 pieces into a working BaseAgent: a valuation oracle estimates what the
energy is worth, a `StrategyPolicy` selects one `StrategyAction`, and the
`OrderProgramCompiler` lowers it to order intents. The runner's RiskGate (M2) then has
final say over what reaches the engine.

This is the scripted precursor to the full `HybridPolicyAgent` (M6): swap the default
`ScriptedStrategyPolicy` for a PPO policy (M4) or an LLM-guided one (M6) and nothing else
changes — the oracle, compiler, and gate are unchanged. Drop-in roster agent: it is a
dataclass with a `price_ref` field, so the scenario loader's cost diversification applies.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import AgentContext, BaseAgent, OrderIntent
from eflux.agents.reflective.strategist import Strategist, StrategyGuidance, apply_guidance
from eflux.agents.strategy import OrderProgramCompiler
from eflux.agents.strategy.policy import ScriptedStrategyPolicy, StrategyPolicy
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.valuation import TruthfulValuationOracle


@dataclass
class StrategyAgent(BaseAgent):
    price_ref: Decimal = Decimal("50.0")
    min_qty: Decimal = Decimal("0.01")
    demand_beta: float = 0.0
    price_cap_mult: float = 1.5
    # Pluggable tactical policy (the PPO/LLM seam). None → scripted baseline.
    policy: StrategyPolicy | None = None

    def __post_init__(self) -> None:
        self._oracle = TruthfulValuationOracle(
            price_ref=self.price_ref,
            demand_beta=self.demand_beta,
            price_cap_mult=self.price_cap_mult,
        )
        self._policy: StrategyPolicy = self.policy or ScriptedStrategyPolicy(min_qty=float(self.min_qty))
        self._compiler = OrderProgramCompiler(min_qty=self.min_qty)

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        valuation = self._oracle.estimate(ctx)
        action = self._policy.select_action(ctx, valuation)
        compiled = self._compiler.compile(ctx, action, valuation)
        # The scripted policy emits no cancel/replace intents; those flow once a
        # repricing policy (CANCEL_REPRICE) is wired through the runner.
        return compiled.order_intents


@dataclass
class HybridPolicyAgent(BaseAgent):
    """The full layered agent (design note §5, §8): a slow LLM strategist coaches a fast
    tactical executor over the structured action space; the Truthful oracle values the
    energy, the compiler lowers the chosen action, and the runner's RiskGate has final
    say (with a Truthful fallback when the executor's batch is fully vetoed).

    LLM guidance enters only as soft priors (apply_guidance) and audit metadata — never
    as a hard order (principles #2, #4, #9). Swap the executor (scripted / PPO / BC) and
    strategist without touching the oracle, compiler, or gate."""

    price_ref: Decimal = Decimal("50.0")
    min_qty: Decimal = Decimal("0.01")
    demand_beta: float = 0.0
    price_cap_mult: float = 1.5
    executor: StrategyPolicy | None = None  # PPO / BC / scripted (default)
    strategist: Strategist | None = None  # slow LLM guidance (off the tick path)
    fallback: BaseAgent | None = None  # safe action when the executor is fully vetoed
    refresh_every_n_ticks: int = 60  # strategist re-query cadence

    def __post_init__(self) -> None:
        self._oracle = TruthfulValuationOracle(
            price_ref=self.price_ref, demand_beta=self.demand_beta, price_cap_mult=self.price_cap_mult
        )
        self._executor: StrategyPolicy = self.executor or ScriptedStrategyPolicy(min_qty=float(self.min_qty))
        self._compiler = OrderProgramCompiler(min_qty=self.min_qty)
        # The runner's gate-fallback hook (M2) reads .risk_fallback.
        self.risk_fallback: BaseAgent = self.fallback or TruthfulAgent(
            price_ref=self.price_ref, demand_beta=self.demand_beta, price_cap_mult=self.price_cap_mult
        )
        self._last_guidance: StrategyGuidance | None = None
        self._ticks = 0
        # Named so the runner's existing _shutdown_reflections cancels it on stop.
        self._reflection_task: asyncio.Task | None = None

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        self._maybe_refresh_guidance(ctx)
        guidance = self.strategist.current_guidance() if self.strategist is not None else None
        self._last_guidance = guidance
        valuation = self._oracle.estimate(ctx)
        action = self._executor.select_action(ctx, valuation, guidance)
        action = apply_guidance(action, guidance)
        compiled = self._compiler.compile(ctx, action, valuation)
        return compiled.order_intents

    def _maybe_refresh_guidance(self, ctx: AgentContext) -> None:
        """Schedule a background strategist refresh on the cadence — never blocking the
        tick (principle #1). Skips silently with no running loop (sync bench/tests), so
        the strategist just keeps serving its cached guidance there."""
        self._ticks += 1
        arefresh = getattr(self.strategist, "arefresh", None)
        if arefresh is None or self._ticks % max(1, self.refresh_every_n_ticks) != 0:
            return
        if self._reflection_task is not None and not self._reflection_task.done():
            return  # a refresh is already in flight
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        m = ctx.market
        self._reflection_task = loop.create_task(
            arefresh(
                recent_pnl=[float(ctx.state.pnl)],
                soc_frac=ctx.battery.soc_frac,
                best_bid=float(m.best_bid) if m.best_bid is not None else None,
                best_ask=float(m.best_ask) if m.best_ask is not None else None,
                last_price=float(m.last_price) if m.last_price is not None else None,
            )
        )

    @property
    def diagnostics(self) -> dict:
        """Current guidance as audit/UI metadata (principle #9) — never execution logic."""
        g = self._last_guidance
        return {
            "guidance": None
            if g is None
            else {
                "preferred_modes": [m.value for m in g.preferred_modes],
                "avoid_modes": [m.value for m in g.avoid_modes],
                "risk_budget": g.risk_budget,
                "soc_target": g.soc_target,
                "execution_style": g.execution_style,
                "lesson": g.lesson,
            },
        }
