"""Merit-order category buckets, shared by the market API and the stats snapshotter.

Lives here (not in api.routers.market, its original home) so the simulator can
classify agents without importing an API router.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from eflux.simulator.runner import SimulatorVPP


def is_llm_vpp(vpp: SimulatorVPP) -> bool:
    # LLM-steered iff the strategist is layered on (llm_enabled). The algorithm=="hybrid" fallback
    # covers any legacy VPP tagged before the basexLLM split.
    return vpp.is_my_vpp and (
        bool(getattr(vpp, "llm_enabled", False)) or getattr(vpp, "algorithm", None) == "hybrid"
    )


def agent_category(vpp: SimulatorVPP) -> str:
    """Coarse merit-order bucket for a built-in VPP, derived from its endowment.

    Checked in merit-order priority: a dedicated gas peaker or wind farm is
    classified by its generator even if it also carries a small battery.
    """
    if is_llm_vpp(vpp):
        return "llm"
    p = vpp.params
    if p.gas_kw_max > 0:
        return "gas"
    if p.wind_kw_rated > 0:
        return "wind"
    if p.pv_kw_peak >= 2.0:
        return "solar"
    return "battery_load"
