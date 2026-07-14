"""Validation policy for immutable Agent Release contracts."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from eflux.db.models import AgentRelease
from eflux.ecosystem.artifacts import hash_file
from eflux.ecosystem.catalog import get_standard_profile
from eflux.ecosystem.errors import EcosystemError

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer_token",
    "credentials",
    "password",
    "private_key",
    "secret",
    "session_token",
}
_PLACEHOLDER_RE = re.compile(r"^(?:\$\{[A-Za-z_][A-Za-z0-9_]*\}|env://[A-Za-z_][A-Za-z0-9_]*)$")
_PLATFORM_BADGES = {
    "Verified Live",
    "Platform Backtested",
    "Fresh-LLM Replay",
    "Reproducible",
    "Online-Adaptive",
    "External Dependency",
    "Self-Reported",
}
_RELEASE_ALGORITHMS = {"truthful", "zip", "gd", "aa", "scripted", "strategy", "ppo"}
_FALLBACK_STRATEGIES = {"safe_hold", "hold", "truthful"}
_RANGE_FIELDS = (
    "battery_kwh_range",
    "battery_kw_max_range",
    "pv_kw_peak_range",
    "load_kw_base_range",
    "wind_kw_rated_range",
    "gas_kw_max_range",
)
_HEX_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_GIT_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_VPP_TYPES = {
    "battery",
    "residential_pv_battery",
    "commercial_load_battery",
    "industrial_flexible_load",
    "renewable_generator",
}


def _looks_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return (
        normalized in _SECRET_KEYS
        or normalized.endswith("_api_key")
        or normalized.endswith("_password")
        or normalized.endswith("_private_key")
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
    )


def reject_embedded_secrets(value: Any, *, path: str = "payload") -> None:
    """Reject credential-shaped values while allowing explicit env placeholders."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if _looks_secret_key(str(key)):
                if not (isinstance(child, str) and _PLACEHOLDER_RE.fullmatch(child.strip())):
                    raise EcosystemError(
                        422,
                        f"{child_path} must be an environment placeholder, not a credential",
                    )
            reject_embedded_secrets(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_embedded_secrets(child, path=f"{path}[{index}]")


def validate_release_badges(badges: list[str]) -> None:
    reserved = sorted(_PLATFORM_BADGES.intersection(badges))
    if reserved:
        raise EcosystemError(
            422,
            "platform evidence badges cannot be self-assigned: " + ", ".join(reserved),
        )


def without_platform_badges(badges: list[str]) -> list[str]:
    """Remove evidence badges that must be re-earned by a forked release."""

    return [badge for badge in badges if badge not in _PLATFORM_BADGES]


def _required_mapping(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise EcosystemError(422, f"{path} must be a non-empty object")
    return value


def _required_text(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EcosystemError(422, f"{path} must be a non-empty string")
    return value.strip()


def _finite_number(value: Any, *, path: str, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise EcosystemError(422, f"{path} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EcosystemError(422, f"{path} must be a finite number") from exc
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        qualifier = f" >= {minimum}" if minimum is not None else ""
        raise EcosystemError(422, f"{path} must be a finite number{qualifier}")
    return number


def _validate_range(value: Any, *, path: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise EcosystemError(422, f"{path} must be a two-item [minimum, maximum] list")
    lower = _finite_number(value[0], path=f"{path}[0]", minimum=0.0)
    upper = _finite_number(value[1], path=f"{path}[1]", minimum=0.0)
    if lower > upper:
        raise EcosystemError(422, f"{path} minimum cannot exceed maximum")
    return lower, upper


def _validate_llm_recipe(value: Any) -> None:
    llm = _required_mapping(value, path="release.recipe.llm")
    for field in ("provider", "model", "system_prompt", "prompt_template"):
        _required_text(llm.get(field), path=f"release.recipe.llm.{field}")
    credential_env = _required_text(
        llm.get("credential_env"), path="release.recipe.llm.credential_env"
    )
    if not _PLACEHOLDER_RE.fullmatch(credential_env):
        raise EcosystemError(
            422, "release.recipe.llm.credential_env must be an env:// or ${NAME} placeholder"
        )
    temperature = _finite_number(
        llm.get("temperature"), path="release.recipe.llm.temperature", minimum=0.0
    )
    if temperature > 2.0:
        raise EcosystemError(422, "release.recipe.llm.temperature must be <= 2")
    max_tokens = llm.get("max_tokens")
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not 1 <= max_tokens <= 32768
    ):
        raise EcosystemError(422, "release.recipe.llm.max_tokens must be an integer in [1, 32768]")
    refresh = llm.get(
        "guidance_refresh_interval_seconds", llm.get("guidance_refresh_every_n_ticks")
    )
    _finite_number(refresh, path="release.recipe.llm.guidance_refresh_interval_seconds", minimum=1)
    memory = _required_mapping(llm.get("memory"), path="release.recipe.llm.memory")
    window = memory.get("window_messages")
    if isinstance(window, bool) or not isinstance(window, int) or window < 0:
        raise EcosystemError(
            422, "release.recipe.llm.memory.window_messages must be a non-negative integer"
        )
    _finite_number(
        llm.get("timeout_seconds", llm.get("timeout_sec")),
        path="release.recipe.llm.timeout_seconds",
        minimum=1,
    )
    _required_text(llm.get("fallback"), path="release.recipe.llm.fallback")
    costs = _required_mapping(llm.get("cost_estimate"), path="release.recipe.llm.cost_estimate")
    for field in ("input_usd_per_million_tokens", "output_usd_per_million_tokens"):
        _finite_number(
            costs.get(field), path=f"release.recipe.llm.cost_estimate.{field}", minimum=0
        )


def _validate_release_recipe(release: AgentRelease) -> None:
    recipe = _required_mapping(release.recipe, path="release.recipe")
    algorithm = _required_text(recipe.get("algorithm"), path="release.recipe.algorithm").lower()
    if algorithm not in _RELEASE_ALGORITHMS:
        raise EcosystemError(
            422,
            f"release.recipe.algorithm must be one of {sorted(_RELEASE_ALGORITHMS)}",
        )
    for field in ("protocol_version", "observation_schema_version", "action_schema_version"):
        version = recipe.get(field)
        if isinstance(version, bool) or str(version).strip() != "1":
            raise EcosystemError(
                422, f"release.recipe.{field} ({field.replace('_', ' ')}) must be 1"
            )
    online_learning = recipe.get("online_learning")
    if not isinstance(online_learning, bool):
        raise EcosystemError(422, "release.recipe.online_learning must be a boolean")
    if online_learning and algorithm != "ppo":
        raise EcosystemError(422, "online learning is supported only for recipe.algorithm='ppo'")
    if online_learning:
        _required_mapping(
            recipe.get("online_learning_params"),
            path="release.recipe.online_learning_params",
        )
    fallback = _required_text(
        recipe.get("fallback_strategy"), path="release.recipe.fallback_strategy"
    ).lower()
    if fallback not in _FALLBACK_STRATEGIES:
        raise EcosystemError(
            422, f"release.recipe.fallback_strategy must be one of {sorted(_FALLBACK_STRATEGIES)}"
        )
    risk = _required_mapping(recipe.get("risk_limits"), path="release.recipe.risk_limits")
    for field in ("max_open_orders", "max_new_orders_per_decision"):
        limit = risk.get(field)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise EcosystemError(
                422, f"release.recipe.risk_limits.{field} must be a positive integer"
            )
    _finite_number(
        risk.get("credit_limit_usd"),
        path="release.recipe.risk_limits.credit_limit_usd",
        minimum=0,
    )
    routing = _required_mapping(recipe.get("order_routing"), path="release.recipe.order_routing")
    markets = routing.get("markets")
    if not isinstance(markets, list) or release.market not in markets:
        raise EcosystemError(
            422, "release.recipe.order_routing.markets must include the Release market"
        )
    invalid_markets = sorted(
        {str(market) for market in markets if market not in {"realprice", "p2p", "hybrid"}}
    )
    if invalid_markets:
        raise EcosystemError(
            422,
            "release.recipe.order_routing.markets contains unsupported values: "
            + ", ".join(invalid_markets),
        )
    default_route = _required_text(
        routing.get("default_route"), path="release.recipe.order_routing.default_route"
    )
    allowed_routes = {
        "realprice": {"grid", "auto"},
        "p2p": {"peer", "auto"},
        "hybrid": {"auto", "peer", "grid"},
    }
    if default_route not in allowed_routes[release.market]:
        raise EcosystemError(
            422,
            f"release.recipe.order_routing.default_route={default_route!r} is invalid for {release.market}",
        )
    if "llm" in recipe and recipe["llm"] is not None:
        if algorithm not in {"scripted", "strategy", "ppo"}:
            raise EcosystemError(
                422,
                "release.recipe.llm is supported only for scripted, strategy, or PPO releases",
            )
        _validate_llm_recipe(recipe["llm"])


def _validate_release_environment(release: AgentRelease) -> None:
    environment = _required_mapping(release.environment, path="release.environment")
    if environment.get("runtime") != "eflux-managed":
        raise EcosystemError(422, "release.environment.runtime must be 'eflux-managed'")
    if environment.get("dependencies_locked") is not True:
        raise EcosystemError(422, "release.environment.dependencies_locked must be true")
    if str(environment.get("agent_protocol_version", "")) != "1":
        raise EcosystemError(422, "release.environment.agent_protocol_version must be 1")
    git_commit = environment.get("git_commit")
    image_digest = environment.get("container_image_digest")
    valid_commit = isinstance(git_commit, str) and _GIT_COMMIT_RE.fullmatch(git_commit.strip())
    valid_digest = (
        isinstance(image_digest, str)
        and image_digest.startswith("sha256:")
        and _HEX_SHA256_RE.fullmatch(image_digest.removeprefix("sha256:"))
    )
    if not valid_commit and not valid_digest:
        raise EcosystemError(
            422,
            "release.environment requires a hexadecimal git_commit or sha256 container_image_digest",
        )


def _validate_release_compatibility(release: AgentRelease) -> None:
    compatibility = _required_mapping(release.compatibility, path="release.compatibility")
    declared_market = compatibility.get("market")
    if declared_market is not None and declared_market != release.market:
        raise EcosystemError(422, "release.compatibility.market must match the Release market")
    profiles: list[str] = []
    if isinstance(compatibility.get("profile_id"), str):
        profiles.append(compatibility["profile_id"])
    if isinstance(compatibility.get("profile_ids"), list):
        profiles.extend(item for item in compatibility["profile_ids"] if isinstance(item, str))
    vpp_types = compatibility.get("vpp_types")
    if not profiles and (not isinstance(vpp_types, list) or not vpp_types):
        raise EcosystemError(
            422, "release.compatibility requires profile_id/profile_ids or non-empty vpp_types"
        )
    for profile_id in profiles:
        try:
            get_standard_profile(profile_id)
        except KeyError as exc:
            raise EcosystemError(
                422, f"release.compatibility references unknown profile {profile_id!r}"
            ) from exc
    if isinstance(vpp_types, list):
        invalid_types = sorted(
            {str(item) for item in vpp_types if not isinstance(item, str) or item not in _VPP_TYPES}
        )
        if invalid_types:
            raise EcosystemError(
                422,
                "release.compatibility.vpp_types contains unsupported values: "
                + ", ".join(invalid_types),
            )
    for field in _RANGE_FIELDS:
        if field in compatibility:
            _validate_range(compatibility[field], path=f"release.compatibility.{field}")
    for field in ("minimum_cash_usd", "minimum_credit_usd"):
        if field in compatibility:
            _finite_number(compatibility[field], path=f"release.compatibility.{field}", minimum=0)
    for field in ("decision_interval_seconds", "product_granularity_seconds"):
        if field in compatibility:
            _finite_number(compatibility[field], path=f"release.compatibility.{field}", minimum=1)


def _validate_release_checkpoint(release: AgentRelease, *, project_root: Path) -> None:
    algorithm = str(release.recipe.get("algorithm", "")).lower()
    state = release.state if isinstance(release.state, dict) else {}
    for archive_field in ("llm_replay_archive", "llm_replay_archives"):
        if archive_field in state and not isinstance(state[archive_field], (dict, list)):
            raise EcosystemError(422, f"release.state.{archive_field} must be an object or list")
    has_checkpoint = "checkpoint_path" in state or "checkpoint_sha256" in state
    if algorithm != "ppo":
        if has_checkpoint:
            raise EcosystemError(422, "only PPO releases may publish checkpoint state")
        return
    raw_path = state.get("checkpoint_path")
    digest = state.get("checkpoint_sha256")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise EcosystemError(422, "release.state.checkpoint_path is required for PPO")
    if not isinstance(digest, str) or not _HEX_SHA256_RE.fullmatch(digest):
        raise EcosystemError(422, "release.state.checkpoint_sha256 must be a hexadecimal SHA-256")
    project_root = project_root.resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        path = candidate.resolve()
    else:
        path = (project_root / candidate).resolve()
    roots = (
        (project_root / "checkpoints").resolve(),
        (project_root / "artifacts" / "training_runs").resolve(),
    )
    if not any(path.is_relative_to(root) for root in roots):
        raise EcosystemError(
            422, "PPO checkpoint must be under checkpoints/ or artifacts/training_runs/"
        )
    if not path.is_file():
        raise EcosystemError(409, "PPO checkpoint is not ready")
    if hash_file(path) != digest.lower():
        raise EcosystemError(422, "PPO checkpoint SHA-256 does not match release state")
    try:
        from eflux.agents.ppo.online_net import load_warm_start

        network = load_warm_start(path)
    except Exception as exc:
        raise EcosystemError(422, f"PPO checkpoint is not loadable: {exc}") from exc
    declared_observation = str(release.recipe.get("observation_schema_version"))
    declared_action = str(release.recipe.get("action_schema_version"))
    if str(network.obs_version) != declared_observation:
        raise EcosystemError(
            422,
            "PPO checkpoint observation schema does not match release.recipe",
        )
    if str(network.encoding_version) != declared_action:
        raise EcosystemError(
            422,
            "PPO checkpoint action schema does not match release.recipe",
        )


def validate_agent_release_for_publish(release: AgentRelease, *, project_root: Path) -> None:
    """Validate the complete, deployable contract before freezing its hash."""

    _validate_release_recipe(release)
    _validate_release_environment(release)
    _validate_release_compatibility(release)
    _validate_release_checkpoint(release, project_root=project_root)


__all__ = [
    "reject_embedded_secrets",
    "validate_agent_release_for_publish",
    "validate_release_badges",
    "without_platform_badges",
]
