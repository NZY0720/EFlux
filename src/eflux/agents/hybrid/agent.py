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
from dataclasses import dataclass, field
from decimal import Decimal

from eflux.agents.base import AgentContext, BaseAgent
from eflux.agents.character import Character, endowment_summary
from eflux.agents.decision import AgentDecision
from eflux.agents.reflective.strategist import (
    Strategist,
    StrategyGuidance,
    apply_guidance,
    compact_forecast_for_strategist,
)
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
    use_forecast: bool = False
    # Pluggable tactical policy (the PPO/LLM seam). None → scripted baseline.
    policy: StrategyPolicy | None = None
    # Endowment-driven personality; NEUTRAL default is a strict identity.
    character: Character = field(default_factory=Character)

    def __post_init__(self) -> None:
        self._oracle = TruthfulValuationOracle(
            price_ref=self.price_ref,
            demand_beta=self.demand_beta,
            price_cap_mult=self.price_cap_mult,
        )
        self._policy: StrategyPolicy = self.policy or ScriptedStrategyPolicy(
            min_qty=float(self.min_qty), use_forecast=self.use_forecast
        )
        self._compiler = OrderProgramCompiler(min_qty=self.min_qty)

    def decide(self, ctx: AgentContext) -> AgentDecision:
        valuation = self._oracle.estimate(ctx)
        action = self._policy.select_action(ctx, valuation)
        action = self.character.apply(action)
        compiled = self._compiler.compile(ctx, action, valuation)
        # The scripted policy emits no cancel/replace intents; those flow once a
        # repricing policy (CANCEL_REPRICE) is wired through the runner.
        return compiled.as_decision()


@dataclass
class HybridPolicyAgent(BaseAgent):
    """The full layered agent (design note §5, §8): a slow LLM strategist coaches a fast
    tactical executor over the structured action space; the Truthful oracle values the
    energy, the compiler lowers the chosen action, and the runner's RiskGate has final
    say (with a configurable fallback policy when the executor's batch is fully vetoed).

    LLM guidance enters through apply_guidance and audit metadata: mode_pin, halt,
    avoid-mode vetoes, and passive-only execution are binding short-window levers.
    Swap the executor (scripted / PPO / BC) and strategist without touching the oracle,
    compiler, or gate."""

    price_ref: Decimal = Decimal("50.0")
    min_qty: Decimal = Decimal("0.01")
    demand_beta: float = 0.0
    price_cap_mult: float = 1.5
    use_forecast: bool = False
    executor: StrategyPolicy | None = None  # PPO / BC / scripted (default)
    strategist: Strategist | None = None  # slow LLM guidance (off the tick path)
    fallback: BaseAgent | None = None  # safe action when the executor is fully vetoed
    fallback_policy: str = "hold"  # "truthful" restores the legacy gate fallback hook
    refresh_every_n_ticks: int = 60  # strategist re-query cadence
    refresh_offset_ticks: int = 0  # stagger LLM calls across a managed fleet
    persona_prompt: str | None = None  # audit metadata copied from AgentSpec.persona
    # Endowment-driven personality; NEUTRAL default is a strict identity.
    character: Character = field(default_factory=Character)

    def __post_init__(self) -> None:
        allowed_fallback_policies = {"truthful", "hold", "noop"}
        if self.fallback_policy not in allowed_fallback_policies:
            allowed = ", ".join(sorted(allowed_fallback_policies))
            raise ValueError(f"fallback_policy must be one of: {allowed}")
        self._oracle = TruthfulValuationOracle(
            price_ref=self.price_ref,
            demand_beta=self.demand_beta,
            price_cap_mult=self.price_cap_mult,
        )
        self._executor: StrategyPolicy = self.executor or ScriptedStrategyPolicy(
            min_qty=float(self.min_qty), use_forecast=self.use_forecast
        )
        self._compiler = OrderProgramCompiler(min_qty=self.min_qty)
        # The runner's gate-fallback hook (M2) reads .risk_fallback.
        self.risk_fallback: BaseAgent | None = self.fallback
        if self.risk_fallback is None and self.fallback_policy == "truthful":
            self.risk_fallback = TruthfulAgent(
                price_ref=self.price_ref,
                demand_beta=self.demand_beta,
                price_cap_mult=self.price_cap_mult,
            )
        self._last_guidance: StrategyGuidance | None = None
        self._ticks = 0
        self._guided_ticks = 0
        self._guidance_changed = 0
        self._mode_overrides = 0
        self._price_dev_sum_bps = 0.0
        self._price_dev_n = 0
        self._last_fair: tuple[float, float] | None = None
        # Named so the runner's existing _shutdown_reflections cancels it on stop.
        self._reflection_task: asyncio.Task | None = None
        # Off-tick PPO update future (online executor + async mode only).
        self._online_task: object | None = None

    def decide(self, ctx: AgentContext) -> AgentDecision:
        self._maybe_refresh_guidance(ctx)
        guidance = self.strategist.current_guidance() if self.strategist is not None else None
        self._last_guidance = guidance
        self._push_meta_and_modes(guidance)
        valuation = self._oracle.estimate(ctx)
        self._last_fair = (valuation.fair_buy_price, valuation.fair_sell_price)
        action = self._executor.select_action(ctx, valuation, guidance)
        pre = action
        action = apply_guidance(action, guidance)
        if guidance is not None:
            self._guided_ticks += 1
            if action != pre:
                self._guidance_changed += 1
            if action.mode != pre.mode:
                self._mode_overrides += 1
        action = self.character.apply(action)
        compiled = self._compiler.compile(ctx, action, valuation)
        self._maybe_online_update(ctx)
        return compiled.as_decision()

    def record_trade(self, record: dict) -> None:
        # Forward fills to a stateful executor (e.g. a BaselinePolicy wrapping AA/ZIP/GD) so its
        # online adaptation keeps learning off the tick path; a PPO/scripted executor has no
        # record_trade and is left untouched.
        fwd = getattr(self._executor, "record_trade", None)
        if callable(fwd):
            fwd(record)
        if self._last_fair is None:
            return
        fair_buy, fair_sell = self._last_fair
        ref = fair_buy if record.get("side") == "buy" else fair_sell
        if ref <= 0:
            return
        try:
            dev_bps = (float(record["price"]) - ref) / ref * 1e4
        except (KeyError, TypeError, ValueError):
            return
        self._price_dev_sum_bps += dev_bps
        self._price_dev_n += 1

    def _push_meta_and_modes(self, guidance: StrategyGuidance | None) -> None:
        """Steer an online PPO executor with the strategist's cached, clamped MetaControl
        (reward weights + learning levers) and preferred/avoid modes. All values are read
        non-blocking off the tick path (principle #1); a non-online executor has no
        apply_meta and is left untouched."""
        apply_meta = getattr(self._executor, "apply_meta", None)
        if apply_meta is None:
            return
        meta = None
        current_meta = getattr(self.strategist, "current_meta", None)
        if callable(current_meta):
            meta = current_meta()
        apply_meta(meta)
        set_modes = getattr(self._executor, "set_guidance_modes", None)
        if set_modes is not None and guidance is not None:
            set_modes(guidance.preferred_modes, guidance.avoid_modes)

    def _maybe_online_update(self, ctx: AgentContext) -> None:
        """Schedule a PPO update off the tick path when the executor learns online in async
        mode. The buffer snapshot + GAE happen synchronously (cheap); the gradient work runs
        on a worker thread and atomically swaps the policy net in. With no running loop
        (bench/tests) it falls back to a synchronous update, keeping those paths deterministic.
        In the default (sync inline) mode the policy updates itself, so this is a no-op."""
        take = getattr(self._executor, "take_update_batch", None)
        learner = getattr(self._executor, "learner", None)
        if take is None or learner is None or getattr(self._executor, "auto_update", True):
            return
        if self._online_task is not None and not self._online_task.done():
            return  # an update is already in flight; let the buffer keep filling
        batch = take()
        if batch is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            learner.optimize(batch)
            return
        self._online_task = loop.run_in_executor(None, learner.optimize, batch)

    def _maybe_refresh_guidance(self, ctx: AgentContext) -> None:
        """Schedule a background strategist refresh on the cadence — never blocking the
        tick (principle #1). Skips silently with no running loop (sync bench/tests), so
        the strategist just keeps serving its cached guidance there."""
        self._ticks += 1
        arefresh = getattr(self.strategist, "arefresh", None)
        interval = max(1, self.refresh_every_n_ticks)
        if arefresh is None or self._ticks % interval != self.refresh_offset_ticks % interval:
            return
        if self._reflection_task is not None and not self._reflection_task.done():
            return  # a refresh is already in flight
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        m = ctx.market
        grid = m.external_market
        self._reflection_task = loop.create_task(
            arefresh(
                recent_pnl=[float(ctx.state.pnl)],
                soc_frac=ctx.battery.soc_frac,
                best_bid=float(m.best_bid) if m.best_bid is not None else None,
                best_ask=float(m.best_ask) if m.best_ask is not None else None,
                last_price=float(m.last_price) if m.last_price is not None else None,
                regime_note=self._regime_note(ctx),
                market_mode=m.market_mode,
                grid_raw_lmp=float(grid.raw_lmp) if grid is not None else None,
                grid_import_price=float(grid.import_price) if grid is not None else None,
                grid_export_price=float(grid.export_price) if grid is not None else None,
                grid_status=grid.status if grid is not None else None,
                forecast=compact_forecast_for_strategist(ctx.forecast),
                endowment=endowment_summary(ctx.params),
                character=self.character.to_public(),
            )
        )

    def _regime_note(self, ctx: AgentContext) -> str:
        """Cheap deterministic market-regime summary for the slow strategist."""

        def _as_float(value) -> float | None:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        m = ctx.market
        ref = float(self.price_ref)
        last = _as_float(m.last_price)
        bid = _as_float(m.best_bid)
        ask = _as_float(m.best_ask)
        soc = ctx.battery.soc_frac
        notes: list[str] = []

        if last is not None and last < 0.3 * ref:
            notes.append(f"price collapsed to {last:.1f} (<30% of ~{ref:.0f}); heavy oversupply")
        if bid is None:
            notes.append("no resting bids — illiquid demand")
        elif bid < 0.3 * ref:
            notes.append(f"best bid {bid:.1f} very low")
        if ask is None:
            notes.append("no resting asks — scarce supply")
        elif ask > 1.3 * ref:
            notes.append(f"best ask {ask:.1f} elevated; scarcity")
        if soc > 0.9:
            notes.append(f"battery near full (SOC {soc:.0%}) — little headroom to buffer")
        elif soc < 0.1:
            notes.append(f"battery near empty (SOC {soc:.0%}) — cannot cover load")

        return "; ".join(notes) or "balanced market"

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
                "mode_pin": None if g.mode_pin is None else g.mode_pin.value,
                "halt": g.halt,
                "passive_only": g.passive_only,
                "risk_budget": g.risk_budget,
                "price_bias_bps": g.price_bias_bps,
                "soc_target": g.soc_target,
                "execution_style": g.execution_style,
                "lesson": g.lesson,
            },
        }

    @property
    def influence_stats(self) -> dict:
        guided = self._guided_ticks
        price_n = self._price_dev_n
        return {
            "guided_ticks": guided,
            "guidance_change_rate": None if guided == 0 else self._guidance_changed / guided,
            "mode_override_rate": None if guided == 0 else self._mode_overrides / guided,
            "avg_price_dev_bps": None if price_n == 0 else self._price_dev_sum_bps / price_n,
        }

    @property
    def reflection_log(self):
        return getattr(self.strategist, "reflection_log", [])

    @property
    def ok_count(self) -> int:
        return getattr(self.strategist, "ok_count", 0)

    @property
    def fail_count(self) -> int:
        return getattr(self.strategist, "fail_count", 0)

    @property
    def skipped_count(self) -> int:
        return getattr(self.strategist, "skipped_count", 0)

    @property
    def last_ok_ts(self):
        return getattr(self.strategist, "last_ok_ts", None)

    @property
    def llm_client(self):
        return getattr(self.strategist, "client", None)
