"""Live PPO policy over the structured StrategyAction space.

`PPOPrimitivePolicy` implements the `StrategyPolicy` seam (M3): it encodes the live
`AgentContext` + `ValuationSignal` exactly as the training env did, runs the RLlib
checkpoint, and decodes the output into a `StrategyAction`. Dropped into a `StrategyAgent`,
it replaces the scripted policy with a learned one — the compiler and RiskGate are
unchanged, so the learned actions are lowered and validated identically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import AgentContext
from eflux.agents.hybrid import StrategyAgent
from eflux.agents.ppo.policy import PPOPolicyWrapper
from eflux.agents.ppo.primitive_encoding import decode_action, encode_obs
from eflux.agents.strategy.schema import StrategyAction, StrategyMode
from eflux.agents.valuation import ValuationSignal

log = logging.getLogger(__name__)

# Must match the env config used in training (so the valuation obs channels line up).
PRIMITIVE_DEMAND_BETA = 0.5


@dataclass
class PPOPrimitivePolicy:
    """A StrategyPolicy backed by a trained RLlib checkpoint."""

    checkpoint_path: str

    def __post_init__(self) -> None:
        from eflux.agents.ppo.primitive_env import VPPPrimitiveEnv

        self._policy = PPOPolicyWrapper(
            self.checkpoint_path,
            env_name="eflux_vpp_primitive",
            env_factory=lambda config: VPPPrimitiveEnv(config),
        )

    def select_action(
        self, ctx: AgentContext, valuation: ValuationSignal, guidance: object | None = None
    ) -> StrategyAction:
        obs = encode_obs(ctx, valuation)
        try:
            vec = self._policy.act(obs)
        except Exception:
            # A failed inference must not stall the market — stand down this tick.
            log.exception("PPO primitive inference failed; emitting NOOP")
            return StrategyAction(mode=StrategyMode.NOOP)
        return decode_action(vec)


def build_ppo_primitive_agent(checkpoint_path: str, *, price_ref: Decimal = Decimal("50.0")) -> StrategyAgent:
    """A StrategyAgent driven by the learned policy — the live PPOPrimitiveAgent.
    Uses the same oracle config (demand_beta) the policy was trained against."""
    return StrategyAgent(
        price_ref=price_ref,
        demand_beta=PRIMITIVE_DEMAND_BETA,
        policy=PPOPrimitivePolicy(checkpoint_path),
    )
