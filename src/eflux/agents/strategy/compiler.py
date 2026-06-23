"""Order-program compiler.

`OrderProgramCompiler.compile(ctx, action, valuation)` is the deterministic translation
from a policy's `StrategyAction` to concrete market intents (design note §4, §8). It:

1. builds the `OrderProgram` for the chosen primitive (via `primitives.build_program`),
2. drops dust / non-positive orders (the same min_qty / price>0 guard Truthful applied),
3. expands the program's cancel policy against the VPP's own resting orders into
   `CancelIntent` / `ReplaceIntent`.

It is pure and independently testable — no market calls, no agent state. The runner (or
RiskGate) is what actually submits the resulting intents.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import (
    AgentContext,
    CancelIntent,
    OpenOrderView,
    OrderIntent,
    ReplaceIntent,
)
from eflux.agents.strategy.primitives import build_program
from eflux.agents.strategy.schema import (
    CancelPolicy,
    CompiledProgram,
    StrategyAction,
)
from eflux.agents.valuation import ValuationSignal


@dataclass
class OrderProgramCompiler:
    # Mirrors the Truthful agent's guard: drop orders below min_qty or with a
    # non-positive price (the matching engine rejects the latter anyway).
    min_qty: Decimal = Decimal("0.01")

    def compile(
        self, ctx: AgentContext, action: StrategyAction, valuation: ValuationSignal
    ) -> CompiledProgram:
        program = build_program(action, ctx, valuation)
        specs = [s for s in program.orders if s.price > 0 and s.qty >= self.min_qty]

        cancel_intents, replace_intents, consumed = self._expand_cancels(
            ctx, program.cancel_policy, specs
        )
        order_intents = [
            OrderIntent(side=s.side, price=s.price, qty=s.qty, dispatched=s.dispatched)
            for i, s in enumerate(specs)
            if i not in consumed
        ]
        return CompiledProgram(
            order_intents=order_intents,
            cancel_intents=cancel_intents,
            replace_intents=replace_intents,
            mode=program.mode,
            rationale=program.rationale,
        )

    def _expand_cancels(
        self, ctx: AgentContext, policy: CancelPolicy, specs: list
    ) -> tuple[list[CancelIntent], list[ReplaceIntent], set[int]]:
        if not (policy.cancel_all or policy.cancel_age_ticks):
            return [], [], set()
        stale = self._stale_orders(ctx, policy)
        if not policy.reprice:
            return [CancelIntent(o.order_id) for o in stale], [], set()

        # Reprice: pair each stale order with a fresh same-side spec → ReplaceIntent;
        # unpaired stale orders become plain cancels, unpaired specs stay as new orders.
        cancels: list[CancelIntent] = []
        replaces: list[ReplaceIntent] = []
        consumed: set[int] = set()
        for o in stale:
            match = next(
                (i for i, s in enumerate(specs) if i not in consumed and s.side == o.side),
                None,
            )
            if match is None:
                cancels.append(CancelIntent(o.order_id))
            else:
                s = specs[match]
                replaces.append(ReplaceIntent(order_id=o.order_id, new_price=s.price, new_qty=s.qty))
                consumed.add(match)
        return cancels, replaces, consumed

    @staticmethod
    def _stale_orders(ctx: AgentContext, policy: CancelPolicy) -> list[OpenOrderView]:
        if policy.cancel_all:
            return list(ctx.open_orders)
        return [o for o in ctx.open_orders if o.age_ticks >= policy.cancel_age_ticks]
