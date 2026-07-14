"""Safe, platform-managed factories for immutable ecosystem artifacts.

This module is intentionally a small allow-list, not a general plug-in loader.  A
Release may select one of the agents already shipped with EFlux and (for PPO) one
checkpoint stored in a platform-owned artifact directory.  It cannot import a
module, contact an endpoint, start a process, or execute a user-provided command.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from eflux.agents.aa_agent import AAAgent
from eflux.agents.base import AgentContext, BaseAgent
from eflux.agents.bench.scenarios import BenchVPP
from eflux.agents.decision import AgentDecision, OrderRequest
from eflux.agents.gd_agent import GDAgent
from eflux.agents.hybrid import HybridPolicyAgent, StrategyAgent
from eflux.agents.llm.strategist import StaticStrategist, StrategyGuidance
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.zip_agent import ZIPAgent
from eflux.config import PROJECT_ROOT
from eflux.ecosystem.catalog import get_standard_profile
from eflux.market.delivery import OrderPurpose
from eflux.vpp.base import VPPParams

_CHECKPOINT_ROOTS = (
    PROJECT_ROOT / "checkpoints",
    PROJECT_ROOT / "artifacts" / "training_runs",
)
_P2P_CHECKPOINT = PROJECT_ROOT / "checkpoints" / "bc_primitive_p2p_v1.pt"
_RESERVED_AGENT_FIELDS = {
    "character",
    "executor",
    "fallback",
    "persona_prompt",
    "policy",
    "strategist",
}
_FORBIDDEN_RUNTIME_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer_token",
    "command",
    "container",
    "credentials",
    "endpoint",
    "entrypoint",
    "password",
    "private_key",
    "secret",
    "subprocess",
    "token",
}


def _value(source: object, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return dict(value)


def _normalized_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_")


def _reject_unsafe_runtime_data(value: object, *, path: str) -> None:
    """Reject executable/network/credential-shaped data at every nesting level."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            key_parts = set(normalized.split("_"))
            if (
                normalized in _FORBIDDEN_RUNTIME_KEYS
                or key_parts.intersection(
                    {
                        "command",
                        "container",
                        "credentials",
                        "endpoint",
                        "entrypoint",
                        "password",
                        "secret",
                        "subprocess",
                        "token",
                    }
                )
                or normalized.endswith("_api_key")
                or normalized.endswith("_endpoint")
                or normalized.endswith("_password")
                or normalized.endswith("_secret")
                or normalized.endswith("_token")
            ):
                raise ValueError(f"{path}.{key} is not allowed in a platform-managed runtime")
            _reject_unsafe_runtime_data(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_unsafe_runtime_data(child, path=f"{path}[{index}]")


def _constructor_params(factory: type[BaseAgent], supplied: object) -> dict[str, Any]:
    params = _mapping(supplied, label="recipe.agent_params")
    allowed = {
        item.name
        for item in fields(factory)
        if item.init and not item.name.startswith("_") and item.name not in _RESERVED_AGENT_FIELDS
    }
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValueError(
            f"agent_params {unknown} not accepted by {factory.__name__}; "
            f"allowed fields are {sorted(allowed)}"
        )
    for key in ("price_ref", "min_qty"):
        if key in params:
            try:
                params[key] = Decimal(str(params[key]))
            except Exception as exc:
                raise ValueError(f"recipe.agent_params.{key} must be a decimal") from exc
    return params


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained_checkpoint(raw_path: object) -> Path:
    if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
        raise ValueError("state.checkpoint_path is required for a PPO Release")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("PPO checkpoint must be an existing platform-managed file") from exc
    if not resolved.is_file():
        raise ValueError("PPO checkpoint must be an existing platform-managed file")
    allowed_roots = tuple(root.resolve() for root in _CHECKPOINT_ROOTS)
    if not any(resolved.is_relative_to(root) for root in allowed_roots):
        raise ValueError("PPO checkpoint must be under checkpoints/ or artifacts/training_runs/")
    return resolved


def _verified_release_checkpoint(state: Mapping[str, Any]) -> Path:
    raw_path = state.get("checkpoint_path", state.get("checkpoint"))
    checkpoint = _contained_checkpoint(raw_path)
    expected = state.get("checkpoint_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("state.checkpoint_sha256 is required for a PPO Release")
    try:
        int(expected, 16)
    except ValueError as exc:
        raise ValueError("state.checkpoint_sha256 must be a hexadecimal SHA-256 digest") from exc
    actual = _sha256_file(checkpoint)
    if actual != expected.lower():
        raise ValueError("PPO checkpoint SHA-256 does not match state.checkpoint_sha256")
    return checkpoint


def _ppo_policy(checkpoint: Path, *, learning: bool, seed: int):
    """Load a known checkpoint without the existing fresh-net fallback."""

    from eflux.agents.ppo.online_net import load_warm_start
    from eflux.agents.ppo.online_ppo import build_online_policy

    # build_online_policy deliberately degrades to a fresh net for live-service
    # resilience.  Artifact evaluation must instead fail closed so its evidence
    # stays bound to the checkpoint named by the Release.
    load_warm_start(str(checkpoint))
    return build_online_policy(str(checkpoint), learning=learning, auto_update=learning, seed=seed)


def verified_release_checkpoint(release_or_mapping: object) -> Path:
    """Return a PPO Release checkpoint only after trusted-path and digest checks."""

    state = _mapping(_value(release_or_mapping, "state"), label="release.state")
    _reject_unsafe_runtime_data(state, path="release.state")
    return _verified_release_checkpoint(state)


def agent_factory_from_release(release_or_mapping: object, *, learning: bool = False) -> BaseAgent:
    """Construct one shipped EFlux agent from an Agent Release.

    Supported ``recipe.algorithm`` values are ``truthful``, ``zip``, ``gd``,
    ``aa``, ``scripted``/``strategy``, and ``ppo``.  PPO is the only algorithm
    that reads Release state, and it requires an exact SHA-256-bound checkpoint
    inside a platform-owned directory.
    """

    recipe = _mapping(_value(release_or_mapping, "recipe"), label="release.recipe")
    state = _mapping(_value(release_or_mapping, "state"), label="release.state")
    _reject_unsafe_runtime_data(recipe, path="release.recipe")
    _reject_unsafe_runtime_data(state, path="release.state")

    algorithm = str(recipe.get("algorithm", "")).strip().lower()
    factories: dict[str, type[BaseAgent]] = {
        "truthful": TruthfulAgent,
        "zip": ZIPAgent,
        "gd": GDAgent,
        "aa": AAAgent,
        "scripted": StrategyAgent,
        "strategy": StrategyAgent,
    }
    if algorithm in factories:
        factory = factories[algorithm]
        return factory(**_constructor_params(factory, recipe.get("agent_params")))
    if algorithm == "ppo":
        params = _constructor_params(StrategyAgent, recipe.get("agent_params"))
        seed = int(recipe.get("seed", 0))
        checkpoint = verified_release_checkpoint(release_or_mapping)
        params["policy"] = _ppo_policy(checkpoint, learning=learning, seed=seed)
        return StrategyAgent(**params)
    supported = sorted([*factories, "ppo"])
    raise ValueError(f"unsupported recipe.algorithm {algorithm!r}; choose from {supported}")


@dataclass
class SeededZeroIntelligenceAgent(BaseAgent):
    """A deliberately simple, reproducibly random P2P benchmark participant."""

    seed: int
    price_ref: Decimal = Decimal("50")
    min_qty: Decimal = Decimal("0.01")
    price_noise_fraction: float = 0.25

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def decide(self, ctx: AgentContext) -> AgentDecision:
        pending = float(ctx.state.pending_net_kwh)
        if abs(pending) < float(self.min_qty):
            return AgentDecision.hold("zero-intelligence participant has no tradable balance")
        side = "sell" if pending > 0.0 else "buy"
        public_ref = ctx.market.mid_price or ctx.market.last_price or self.price_ref
        noise = self._rng.uniform(-self.price_noise_fraction, self.price_noise_fraction)
        price = max(0.0001, float(public_ref) * (1.0 + noise))
        qty_fraction = self._rng.uniform(0.25, 1.0)
        qty = Decimal(str(abs(pending) * qty_fraction)).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )
        if qty < self.min_qty:
            return AgentDecision.hold("zero-intelligence quantity is below the minimum")
        return AgentDecision(
            orders=(
                OrderRequest(
                    side=side,
                    price=Decimal(str(price)).quantize(Decimal("0.0001")),
                    qty_kwh=qty,
                    interval=ctx.primary_interval,
                    purpose=OrderPurpose.BALANCE,
                    ttl_sec=ctx.decision_interval_sec,
                ),
            ),
            rationale="seeded zero-intelligence quote",
        )


@dataclass
class AdversarialPressureAgent(SeededZeroIntelligenceAgent):
    """Hostile-price probe that still passes through the ordinary gateway rules."""

    price_noise_fraction: float = 0.75


@dataclass
class ArchivedGuidanceHybridAgent(HybridPolicyAgent):
    """Offline Hybrid using platform-archived static guidance and no network client."""

    guidance_source: str = "platform_archived_static"


def _platform_p2p_checkpoint() -> Path:
    if _P2P_CHECKPOINT.is_file():
        return _contained_checkpoint(_P2P_CHECKPOINT)
    raise ValueError("the platform P2P PPO checkpoint is unavailable")


def _population_agent(strategy: str, *, seed: int) -> BaseAgent:
    common = {"price_ref": Decimal("50")}
    if strategy == "truthful":
        return TruthfulAgent(**common)
    if strategy == "zip":
        return ZIPAgent(**common)
    if strategy == "gd":
        return GDAgent(**common)
    if strategy == "aa":
        return AAAgent(**common)
    if strategy == "zero_intelligence":
        return SeededZeroIntelligenceAgent(seed=seed, **common)
    if strategy == "adversarial":
        return AdversarialPressureAgent(seed=seed, **common)
    if strategy in {"ppo", "llm_hybrid"}:
        policy = _ppo_policy(_platform_p2p_checkpoint(), learning=False, seed=seed)
        if strategy == "ppo":
            return StrategyAgent(policy=policy, use_forecast=True, **common)
        guidance = StrategyGuidance(
            risk_budget=0.8,
            passive_only=True,
            execution_style="archived passive-liquidity guidance",
            lesson="Static benchmark guidance; no live model call.",
        )
        return ArchivedGuidanceHybridAgent(
            executor=policy,
            strategist=StaticStrategist(guidance=guidance),
            use_forecast=True,
            **common,
        )
    raise ValueError(f"unsupported population strategy {strategy!r}")


def _population_params(profile_id: str, scenario: Mapping[str, Any]) -> VPPParams:
    profile = get_standard_profile(profile_id)
    raw = dict(profile["spec"]["vpp_params"])
    renewable = float(scenario.get("renewable_multiplier", 1.0))
    load = float(scenario.get("load_multiplier", 1.0))
    storage = float(scenario.get("storage_multiplier", 1.0))
    for key in ("pv_kw_peak", "wind_kw_rated"):
        raw[key] = float(raw.get(key, 0.0)) * renewable
    raw["load_kw_base"] = float(raw.get("load_kw_base", 0.0)) * load
    for key in ("battery_kwh", "battery_kw_max"):
        raw[key] = float(raw.get(key, 0.0)) * storage
    return VPPParams.from_dict(raw)


def bench_roster_from_population(pack_mapping: object, seed: int) -> list[BenchVPP]:
    """Expand a catalog Population Pack into a deterministic benchmark roster."""

    _reject_unsafe_runtime_data(pack_mapping, path="population_pack")
    spec = _mapping(_value(pack_mapping, "spec"), label="population_pack.spec")
    roster = spec.get("roster")
    if not isinstance(roster, list) or not roster:
        raise ValueError("population_pack.spec.roster must be a non-empty list")
    scenario = _mapping(spec.get("scenario"), label="population_pack.spec.scenario")
    pack_id = str(_value(pack_mapping, "id", "population")).strip() or "population"
    rng = random.Random(seed)
    result: list[BenchVPP] = []
    for cohort_index, raw_cohort in enumerate(roster):
        cohort = _mapping(raw_cohort, label=f"population_pack.spec.roster[{cohort_index}]")
        strategy = str(cohort.get("strategy", "")).strip().lower()
        count = int(cohort.get("count", 0))
        pool = cohort.get("profile_pool")
        if count <= 0:
            raise ValueError(f"population cohort {cohort_index} count must be positive")
        if (
            not isinstance(pool, list)
            or not pool
            or not all(isinstance(item, str) for item in pool)
        ):
            raise ValueError(f"population cohort {cohort_index} profile_pool must be non-empty")
        for member_index in range(count):
            profile_id = rng.choice(pool)
            member_seed = rng.randrange(0, 2**31)
            result.append(
                BenchVPP(
                    name=(f"{pack_id}-{strategy}-{cohort_index + 1:02d}-{member_index + 1:03d}"),
                    params=_population_params(profile_id, scenario),
                    agent=_population_agent(strategy, seed=member_seed),
                    seed=member_seed,
                )
            )
    return result


__all__ = [
    "AdversarialPressureAgent",
    "ArchivedGuidanceHybridAgent",
    "SeededZeroIntelligenceAgent",
    "agent_factory_from_release",
    "bench_roster_from_population",
    "verified_release_checkpoint",
]
