"""Stable built-in benchmark catalog.

The catalog is deliberately data-only: entries can be rendered by the API/UI or
consumed by evaluation workers without importing simulator objects.  Every entry
has a canonical content hash, and callers receive deep copies so a request cannot
mutate process-wide benchmark definitions.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

_CATALOG_VERSION = "1.0.0"
_STANDARD_SEEDS = [11, 23, 37, 53, 71]


def _canonical_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _benchmark_defaults() -> dict[str, Any]:
    return {
        "decision_interval_seconds": 300,
        "gate_closure_seconds": 60,
        "execution_latency_ms": 250,
        "forecast": {"horizon_intervals": 12, "noise_std": 0.1},
        "fees": {"maker_bps": 0.0, "taker_bps": 0.0},
        "imbalance": {
            "settlement": "external_reference_price",
            "penalty_usd_per_mwh": 100.0,
        },
    }


def _profile(
    profile_id: str,
    name: str,
    description: str,
    asset_type: str,
    vpp_params: dict[str, Any],
) -> dict[str, Any]:
    content = {
        "id": profile_id,
        "version": _CATALOG_VERSION,
        "name": name,
        "description": description,
        "market": "realprice",
        "spec": {
            "schema_version": "1",
            "asset_type": asset_type,
            "vpp_params": vpp_params,
            "benchmark_defaults": _benchmark_defaults(),
        },
    }
    return {**content, "content_sha256": _canonical_sha256(content)}


_STANDARD_PROFILES = (
    _profile(
        "battery-only",
        "Battery-only",
        "Grid-connected battery with no native generation or load.",
        "battery",
        {
            "pv_kw_peak": 0.0,
            "wind_kw_rated": 0.0,
            "load_kw_base": 0.0,
            "load_profile": "flat",
            "battery_kwh": 100.0,
            "battery_kw_max": 50.0,
            "battery_eta_rt": 0.9,
            "battery_initial_soc_frac": 0.5,
            "battery_degradation_cost_per_mwh_throughput": 20.0,
        },
    ),
    _profile(
        "residential-pv-battery",
        "Residential PV + Battery",
        "Residential demand, rooftop solar, and a behind-the-meter battery.",
        "residential_pv_battery",
        {
            "pv_kw_peak": 8.0,
            "wind_kw_rated": 0.0,
            "load_kw_base": 2.5,
            "load_profile": "residential",
            "load_elasticity": 0.15,
            "battery_kwh": 20.0,
            "battery_kw_max": 6.0,
            "battery_eta_rt": 0.9,
            "battery_initial_soc_frac": 0.5,
            "battery_degradation_cost_per_mwh_throughput": 20.0,
        },
    ),
    _profile(
        "commercial-load-battery",
        "Commercial Load + Battery",
        "Day-peaking commercial demand backed by a medium-duration battery.",
        "commercial_load_battery",
        {
            "pv_kw_peak": 0.0,
            "wind_kw_rated": 0.0,
            "load_kw_base": 120.0,
            "load_profile": "commercial",
            "load_elasticity": 0.2,
            "battery_kwh": 200.0,
            "battery_kw_max": 75.0,
            "battery_eta_rt": 0.9,
            "battery_initial_soc_frac": 0.5,
            "battery_degradation_cost_per_mwh_throughput": 20.0,
        },
    ),
    _profile(
        "industrial-flexible-load",
        "Industrial Flexible Load",
        "Large industrial demand with material load-shifting flexibility.",
        "industrial_flexible_load",
        {
            "pv_kw_peak": 0.0,
            "wind_kw_rated": 0.0,
            "load_kw_base": 500.0,
            "load_profile": "industrial",
            "load_elasticity": 0.35,
            "battery_kwh": 0.0,
            "battery_kw_max": 0.0,
            "battery_initial_soc_frac": 0.0,
        },
    ),
    _profile(
        "renewable-generator",
        "Renewable Generator",
        "Utility-scale hybrid solar and wind generator without native demand.",
        "renewable_generator",
        {
            "pv_kw_peak": 500.0,
            "wind_kw_rated": 750.0,
            "wind_mean_speed": 8.5,
            "load_kw_base": 0.0,
            "load_profile": "flat",
            "battery_kwh": 0.0,
            "battery_kw_max": 0.0,
            "battery_initial_soc_frac": 0.0,
        },
    ),
)


def _cohort(strategy: str, count: int, profile_pool: list[str]) -> dict[str, Any]:
    return {"strategy": strategy, "count": count, "profile_pool": profile_pool}


def _population_pack(
    pack_id: str,
    name: str,
    description: str,
    category: str,
    roster: list[dict[str, Any]],
    *,
    renewable_multiplier: float = 1.0,
    load_multiplier: float = 1.0,
    storage_multiplier: float = 1.0,
    liquidity: str = "normal",
    adversarial_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenario = {
        "renewable_multiplier": renewable_multiplier,
        "load_multiplier": load_multiplier,
        "storage_multiplier": storage_multiplier,
        "liquidity": liquidity,
    }
    if adversarial_rules is not None:
        scenario["adversarial_rules"] = adversarial_rules
    content = {
        "id": pack_id,
        "version": _CATALOG_VERSION,
        "name": name,
        "description": description,
        "market": "p2p",
        "spec": {
            "schema_version": "1",
            "category": category,
            "market_mechanism": "continuous_double_auction",
            "candidate_slots": 1,
            "roster": roster,
            "scenario": scenario,
            "evaluation_protocol": {
                "method": "paired_world_population_tournament",
                "control_strategy": "truthful",
                "seeds": _STANDARD_SEEDS,
                "intervals_per_episode": 288,
            },
        },
    }
    return {**content, "content_sha256": _canonical_sha256(content)}


_BATTERY_POOL = ["battery-only", "residential-pv-battery", "commercial-load-battery"]
_BALANCED_POOL = [
    "residential-pv-battery",
    "commercial-load-battery",
    "industrial-flexible-load",
    "renewable-generator",
]

_BUILTIN_POPULATION_PACKS = (
    _population_pack(
        "high-renewable-surplus",
        "High Renewable Surplus",
        "Generation-heavy roster that stresses surplus absorption and curtailment behavior.",
        "supply_surplus",
        [
            _cohort("truthful", 12, ["renewable-generator"]),
            _cohort("zip", 4, ["renewable-generator", "residential-pv-battery"]),
            _cohort("truthful", 4, ["battery-only"]),
        ],
        renewable_multiplier=1.6,
        load_multiplier=0.7,
    ),
    _population_pack(
        "demand-tight",
        "Demand Tight",
        "Demand-heavy roster with scarce supply and a higher imbalance-risk regime.",
        "demand_tightness",
        [
            _cohort("truthful", 10, ["industrial-flexible-load"]),
            _cohort("aa", 6, ["commercial-load-battery"]),
            _cohort("truthful", 4, ["renewable-generator"]),
        ],
        renewable_multiplier=0.65,
        load_multiplier=1.4,
    ),
    _population_pack(
        "high-liquidity-market-making",
        "High Liquidity Market Making",
        "Dense two-sided quoting from adaptive market makers.",
        "liquidity",
        [
            _cohort("zip", 8, _BALANCED_POOL),
            _cohort("aa", 8, _BALANCED_POOL),
            _cohort("gd", 4, _BALANCED_POOL),
        ],
        liquidity="high",
    ),
    _population_pack(
        "low-liquidity",
        "Low Liquidity",
        "Sparse intermittent quoting that stresses execution risk and unfilled orders.",
        "liquidity",
        [
            _cohort("truthful", 5, _BALANCED_POOL),
            _cohort("zero_intelligence", 3, _BALANCED_POOL),
        ],
        liquidity="low",
    ),
    _population_pack(
        "battery-arbitrage-heavy",
        "Battery Arbitrage Heavy",
        "Storage-dense population where many agents compete for the same temporal spread.",
        "storage_competition",
        [
            _cohort("ppo", 8, _BATTERY_POOL),
            _cohort("zip", 6, _BATTERY_POOL),
            _cohort("truthful", 6, _BATTERY_POOL),
        ],
        storage_multiplier=1.8,
    ),
    _population_pack(
        "truthful-majority",
        "Truthful Majority",
        "Cost-based reference population with a small adaptive-strategy minority.",
        "strategy_mix",
        [
            _cohort("truthful", 16, _BALANCED_POOL),
            _cohort("zip", 2, _BALANCED_POOL),
            _cohort("aa", 2, _BALANCED_POOL),
        ],
    ),
    _population_pack(
        "zi-majority",
        "ZI Majority",
        "Zero-intelligence-dominant population for a weakly strategic market baseline.",
        "strategy_mix",
        [
            _cohort("zero_intelligence", 16, _BALANCED_POOL),
            _cohort("truthful", 4, _BALANCED_POOL),
        ],
    ),
    _population_pack(
        "ppo-llm-mixed",
        "PPO / LLM Mixed",
        "Learned-policy population mixing PPO executors and LLM-guided hybrids.",
        "strategy_mix",
        [
            _cohort("ppo", 8, _BALANCED_POOL),
            _cohort("llm_hybrid", 8, _BALANCED_POOL),
            _cohort("truthful", 4, _BALANCED_POOL),
        ],
    ),
    _population_pack(
        "adversarial",
        "Adversarial",
        "Hostile roster that probes manipulation resistance without granting rule exemptions.",
        "adversarial_robustness",
        [
            _cohort("adversarial", 6, _BALANCED_POOL),
            _cohort("zero_intelligence", 4, _BALANCED_POOL),
            _cohort("truthful", 10, _BALANCED_POOL),
        ],
        liquidity="stressed",
        adversarial_rules={
            "strategies": ["quote_stuffing", "liquidity_withdrawal", "price_pressure"],
            "must_pass_standard_gateway": True,
            "privileged_information": False,
        },
    ),
)

_STANDARD_PROFILE_BY_ID = {item["id"]: item for item in _STANDARD_PROFILES}


def list_standard_profiles() -> list[dict[str, Any]]:
    """Return the five versioned Realprice asset profiles in stable order."""

    return deepcopy(list(_STANDARD_PROFILES))


def get_standard_profile(profile_id: str) -> dict[str, Any]:
    """Return one asset profile by stable id.

    Raises ``KeyError`` when ``profile_id`` is not a built-in profile.
    """

    try:
        return deepcopy(_STANDARD_PROFILE_BY_ID[profile_id])
    except KeyError:
        raise KeyError(f"unknown standard profile: {profile_id}") from None


def list_builtin_population_packs() -> list[dict[str, Any]]:
    """Return the nine versioned P2P population packs in stable order."""

    return deepcopy(list(_BUILTIN_POPULATION_PACKS))
