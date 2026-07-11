"""Order-program compiler.

`OrderProgramCompiler.compile(ctx, action, valuation)` is the deterministic translation
from a policy's `StrategyAction` to concrete V2 requests. It:

1. builds the `OrderProgram` for the chosen primitive (via `primitives.build_program`),
2. drops dust quantities and non-finite signed prices,
3. expands the program's cancel policy against the VPP's own resting orders into
   `CancelRequest` / `ReplaceRequest`.

It is pure and independently testable — no market calls, no agent state. The runner
submits the resulting decision through TradingGatewayV2.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import AgentContext, OpenOrderView
from eflux.agents.decision import CancelRequest, OrderRequest, ReplaceRequest
from eflux.agents.strategy.primitives import build_program
from eflux.agents.strategy.schema import (
    CancelPolicy,
    CompiledProgram,
    StrategyAction,
)
from eflux.agents.valuation import ValuationSignal


@dataclass
class OrderProgramCompiler:
    # Prices are signed in V2; only non-finite prices and dust quantities drop.
    min_qty: Decimal = Decimal("0.01")

    def compile(
        self, ctx: AgentContext, action: StrategyAction, valuation: ValuationSignal
    ) -> CompiledProgram:
        program = build_program(action, ctx, valuation)
        specs = [s for s in program.orders if s.price.is_finite() and s.qty >= self.min_qty]

        cancel_requests, replace_requests, consumed = self._expand_cancels(
            ctx, program.cancel_policy, specs
        )
        order_requests = [self._request(ctx, s) for i, s in enumerate(specs) if i not in consumed]
        return CompiledProgram(
            order_requests=order_requests,
            cancel_requests=cancel_requests,
            replace_requests=replace_requests,
            mode=program.mode,
            rationale=program.rationale,
        )

    def _expand_cancels(
        self, ctx: AgentContext, policy: CancelPolicy, specs: list
    ) -> tuple[list[CancelRequest], list[ReplaceRequest], set[int]]:
        if not (policy.cancel_all or policy.cancel_age_ticks):
            return [], [], set()
        stale = self._stale_orders(ctx, policy)
        if not policy.reprice:
            return [CancelRequest(o.order_id) for o in stale], [], set()

        # Reprice: pair each stale order with a fresh same-side spec → ReplaceRequest;
        # unpaired stale orders become plain cancels, unpaired specs stay as new orders.
        cancels: list[CancelRequest] = []
        replaces: list[ReplaceRequest] = []
        consumed: set[int] = set()
        for o in stale:
            match = next(
                (i for i, s in enumerate(specs) if i not in consumed and s.side == o.side),
                None,
            )
            if match is None:
                cancels.append(CancelRequest(o.order_id))
            else:
                s = specs[match]
                replaces.append(ReplaceRequest(o.order_id, self._request(ctx, s)))
                consumed.add(match)
        return cancels, replaces, consumed

    @staticmethod
    def _request(ctx: AgentContext, spec) -> OrderRequest:
        ttl_sec = (
            spec.ttl_ticks * ctx.decision_interval_sec
            if spec.ttl_ticks > 0
            else ctx.decision_interval_sec
        )
        return OrderRequest(
            side=spec.side,
            price=spec.price,
            qty_kwh=spec.qty,
            interval=ctx.primary_interval,
            purpose=spec.purpose,
            ttl_sec=ttl_sec,
        )

    @staticmethod
    def _stale_orders(ctx: AgentContext, policy: CancelPolicy) -> list[OpenOrderView]:
        if policy.cancel_all:
            return list(ctx.open_orders)
        return [o for o in ctx.open_orders if o.age_ticks >= policy.cancel_age_ticks]
