"""Benchmark metrics and leaderboard formatting.

Reward should not be immediate realized PnL alone (design note §7): we also track
mark-to-market on unsettled inventory, energy actually traded, residual imbalance, final
SOC, and invalid actions (gate vetoes). These are the dimensions later milestones'
reward shaping must answer to.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EpisodeMetrics:
    candidate: str
    realized_pnl: float  # cash from fills
    mark_to_market: float  # realized + unsettled inventory valued at last price
    energy_bought_kwh: float
    energy_sold_kwh: float
    unresolved_imbalance_kwh: float  # |pending + resting book exposure| at episode end
    final_soc_frac: float
    risk_rejections: int  # gate vetoes (invalid actions)
    n_ticks: int

    @property
    def energy_traded_kwh(self) -> float:
        return self.energy_bought_kwh + self.energy_sold_kwh


def format_leaderboard(rows: list[EpisodeMetrics]) -> str:
    rows = sorted(rows, key=lambda m: m.mark_to_market, reverse=True)
    header = (
        f"{'candidate':<12}{'realized_pnl':>14}{'mark_to_mkt':>14}"
        f"{'energy_kwh':>12}{'imbalance':>11}{'soc':>7}{'rejects':>9}"
    )
    lines = [header, "-" * len(header)]
    for m in rows:
        lines.append(
            f"{m.candidate:<12}{m.realized_pnl:>14.3f}{m.mark_to_market:>14.3f}"
            f"{m.energy_traded_kwh:>12.3f}{m.unresolved_imbalance_kwh:>11.3f}"
            f"{m.final_soc_frac:>7.2f}{m.risk_rejections:>9d}"
        )
    return "\n".join(lines)
