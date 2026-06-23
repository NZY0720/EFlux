"""Fixed benchmark scenario: one agent-under-test vs a deterministic counter-roster.

Every candidate occupies the same DER slot and faces the same counterparties (fresh,
identically-seeded agents per episode), so differences in the scoreboard are differences
in policy, not luck.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from eflux.agents.base import BaseAgent
from eflux.agents.gas import GasGeneratorAgent
from eflux.agents.hybrid import StrategyAgent
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.zi import ZIAgent
from eflux.vpp.base import VPPParams


@dataclass
class BenchVPP:
    name: str
    params: VPPParams
    agent: BaseAgent
    seed: int


def counter_roster() -> list[BenchVPP]:
    """Two-sided, stable liquidity: a constant load buyer, a big PV seller, ZI noise,
    and a gas backstop that caps the price. Fresh agent instances each call (agents
    carry per-tick state) so episodes never share mutable state."""
    return [
        BenchVPP(
            "buyer-load",
            VPPParams(pv_kw_peak=0.0, battery_kwh=15.0, load_kw_base=6.0),
            TruthfulAgent(price_ref=Decimal("50")),
            101,
        ),
        BenchVPP(
            "seller-pv",
            VPPParams(pv_kw_peak=16.0, battery_kwh=15.0, load_kw_base=2.0, markup_floor=0.4),
            TruthfulAgent(price_ref=Decimal("50")),
            102,
        ),
        BenchVPP(
            "zi-noise",
            VPPParams(pv_kw_peak=6.0, battery_kwh=10.0, load_kw_base=4.0),
            ZIAgent(price_ref=Decimal("50")),
            103,
        ),
        BenchVPP(
            "gas-backstop",
            VPPParams(gas_kw_max=20.0, gas_cost_per_kwh=65.0, load_kw_base=0.0),
            GasGeneratorAgent(),
            104,
        ),
    ]


def test_slot_params() -> VPPParams:
    """The agent-under-test endowment: a prosumer that is surplus midday and deficit at
    night, so a full sim-day exercises both LIQUIDATE_SURPLUS and COVER_DEFICIT."""
    return VPPParams(
        pv_kw_peak=8.0, battery_kwh=15.0, battery_kw_max=4.0, load_kw_base=4.0, markup_floor=0.4
    )


def candidates() -> dict[str, Callable[[], BaseAgent]]:
    """Agents to score, each constructed fresh per episode."""
    return {
        "truthful": lambda: TruthfulAgent(price_ref=Decimal("50")),
        "strategy": lambda: StrategyAgent(price_ref=Decimal("50")),
        "zi": lambda: ZIAgent(price_ref=Decimal("50")),
    }
