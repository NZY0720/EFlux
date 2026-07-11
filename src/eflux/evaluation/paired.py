"""Isolated paired-world evaluation for strategy uplift.

Treatment and control never coexist in one market.  Each pair receives the
same exogenous seed, clock, DER endowment, and freshly constructed counterparty
roster; only the candidate factory changes.  Pairwise subtraction therefore
removes seed/weather/load noise without the interference caused by live twins.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from statistics import fmean, median

from eflux.agents.base import BaseAgent
from eflux.agents.bench.metrics import EpisodeMetrics
from eflux.agents.bench.run import score
from eflux.vpp.base import VPPParams


@dataclass(frozen=True, slots=True)
class PairedSeedResult:
    seed: int
    treatment: EpisodeMetrics
    control: EpisodeMetrics
    mark_to_market_uplift_usd: float
    realized_pnl_uplift_usd: float
    imbalance_reduction_kwh: float
    rejection_reduction: int
    energy_traded_delta_kwh: float
    final_soc_delta: float


@dataclass(frozen=True, slots=True)
class PairedEvaluationReport:
    treatment_name: str
    control_name: str
    interval_count: int
    pairs: tuple[PairedSeedResult, ...]
    mean_mark_to_market_uplift_usd: float
    median_mark_to_market_uplift_usd: float
    treatment_win_rate: float
    mean_imbalance_reduction_kwh: float
    mean_rejection_reduction: float

    def to_dict(self) -> dict:
        return {
            "treatment_name": self.treatment_name,
            "control_name": self.control_name,
            "interval_count": self.interval_count,
            "pair_count": len(self.pairs),
            "mean_mark_to_market_uplift_usd": self.mean_mark_to_market_uplift_usd,
            "median_mark_to_market_uplift_usd": self.median_mark_to_market_uplift_usd,
            "treatment_win_rate": self.treatment_win_rate,
            "mean_imbalance_reduction_kwh": self.mean_imbalance_reduction_kwh,
            "mean_rejection_reduction": self.mean_rejection_reduction,
            "pairs": [
                {
                    **asdict(pair),
                    "treatment": asdict(pair.treatment),
                    "control": asdict(pair.control),
                }
                for pair in self.pairs
            ],
        }


def evaluate_paired_worlds(
    *,
    treatment_name: str,
    treatment_factory: Callable[[], BaseAgent],
    control_name: str,
    control_factory: Callable[[], BaseAgent],
    seeds: tuple[int, ...] | list[int],
    interval_count: int,
    forecasts_enabled: bool = True,
    candidate_params: VPPParams | None = None,
    market_price_ref: Decimal | None = None,
    market_mode: str = "p2p",
) -> PairedEvaluationReport:
    """Run isolated treatment/control worlds and aggregate paired deltas."""

    seed_values = tuple(int(seed) for seed in seeds)
    if not seed_values:
        raise ValueError("paired evaluation requires at least one seed")
    if len(seed_values) != len(set(seed_values)):
        raise ValueError("paired evaluation seeds must be unique")
    if interval_count <= 0:
        raise ValueError("interval_count must be positive")

    pairs: list[PairedSeedResult] = []
    for seed in seed_values:
        treatment = score(
            treatment_name,
            treatment_factory,
            n_ticks=interval_count,
            tick_h=5.0 / 60.0,
            forecasts_enabled=forecasts_enabled,
            episode_seed=seed,
            candidate_params=candidate_params,
            market_price_ref=market_price_ref,
            market_mode=market_mode,
        )
        control = score(
            control_name,
            control_factory,
            n_ticks=interval_count,
            tick_h=5.0 / 60.0,
            forecasts_enabled=forecasts_enabled,
            episode_seed=seed,
            candidate_params=candidate_params,
            market_price_ref=market_price_ref,
            market_mode=market_mode,
        )
        pairs.append(
            PairedSeedResult(
                seed=seed,
                treatment=treatment,
                control=control,
                mark_to_market_uplift_usd=treatment.mark_to_market - control.mark_to_market,
                realized_pnl_uplift_usd=treatment.realized_pnl - control.realized_pnl,
                imbalance_reduction_kwh=(
                    control.unresolved_imbalance_kwh - treatment.unresolved_imbalance_kwh
                ),
                rejection_reduction=control.risk_rejections - treatment.risk_rejections,
                energy_traded_delta_kwh=(treatment.energy_traded_kwh - control.energy_traded_kwh),
                final_soc_delta=treatment.final_soc_frac - control.final_soc_frac,
            )
        )

    uplifts = [pair.mark_to_market_uplift_usd for pair in pairs]
    return PairedEvaluationReport(
        treatment_name=treatment_name,
        control_name=control_name,
        interval_count=interval_count,
        pairs=tuple(pairs),
        mean_mark_to_market_uplift_usd=fmean(uplifts),
        median_mark_to_market_uplift_usd=median(uplifts),
        treatment_win_rate=sum(uplift > 0.0 for uplift in uplifts) / len(uplifts),
        mean_imbalance_reduction_kwh=fmean(pair.imbalance_reduction_kwh for pair in pairs),
        mean_rejection_reduction=fmean(pair.rejection_reduction for pair in pairs),
    )
