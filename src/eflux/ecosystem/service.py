"""Persistence and policy boundaries for the EFlux artifact ecosystem.

This module is deliberately an artifact registry and job queue.  It never imports
container, checkpoint, subprocess, or training runtimes: user-supplied release
metadata can be inspected and queued for platform workers, but is never executed
by an API request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from eflux.config import PROJECT_ROOT, get_settings
from eflux.datasets.trajectory import (
    DATASET_SCHEMA_VERSION,
    build_trajectory_rows,
    export_trajectory_jsonl_gz,
    inspect_trajectory_artifact,
)
from eflux.db.models import (
    AgentRelease,
    AuditEvent,
    BehaviorDataset,
    DatasetTrainingRun,
    MarketAuditEvent,
    MarketSession,
    PopulationPack,
    ReleaseEvaluation,
    User,
    VppStatSnapshot,
)
from eflux.ecosystem.catalog import get_standard_profile

PUBLIC_STATUSES = ("published", "verified")
DATASET_ARTIFACTS_BASE = PROJECT_ROOT / "artifacts" / "behavior_datasets"
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


class EcosystemError(Exception):
    """A stable service error that the HTTP router can translate directly."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _is_admin(user: User | None) -> bool:
    if user is None:
        return False
    settings = get_settings()
    return user.role == "admin" or user.email.strip().lower() in settings.admin_email_set


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def dataset_attestation_payload(
    dataset: BehaviorDataset, *, provider_id: str, issued_at: datetime, artifact_sha256: str
) -> dict[str, Any]:
    """Build the exact, dataset-bound payload a trusted external provider signs."""

    return {
        "schema": "eflux.external-dataset-attestation.v1",
        "provider_id": provider_id,
        "issued_at": _utc_iso(issued_at),
        "dataset": {
            "id": dataset.id,
            "owner_id": dataset.owner_id,
            "name": dataset.name,
            "version": dataset.version,
            "market": dataset.market,
            "schema_version": dataset.schema_version,
            "artifact_sha256": artifact_sha256,
        },
    }


def canonical_attestation_payload(payload: dict[str, Any]) -> str:
    """Return the UTF-8 text signed by external attestation providers."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


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


def _validate_release_badges(badges: list[str], _user: User) -> None:
    reserved = sorted(_PLATFORM_BADGES.intersection(badges))
    if reserved:
        raise EcosystemError(
            422,
            "platform evidence badges cannot be self-assigned: " + ", ".join(reserved),
        )


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
        if (
            isinstance(version, bool)
            or not isinstance(version, (str, int))
            or not str(version).strip()
        ):
            raise EcosystemError(
                422, f"release.recipe.{field} must be a non-empty string or integer"
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
    if str(environment.get("agent_protocol_version", "")) != "2":
        raise EcosystemError(422, "release.environment.agent_protocol_version must be 2")
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


def _validate_release_checkpoint(release: AgentRelease) -> None:
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
    candidate = Path(raw_path)
    if candidate.is_absolute():
        path = candidate.resolve()
    else:
        path = (PROJECT_ROOT / candidate).resolve()
    roots = (
        (PROJECT_ROOT / "checkpoints").resolve(),
        (PROJECT_ROOT / "artifacts" / "training_runs").resolve(),
    )
    if not any(path.is_relative_to(root) for root in roots):
        raise EcosystemError(
            422, "PPO checkpoint must be under checkpoints/ or artifacts/training_runs/"
        )
    if not path.is_file():
        raise EcosystemError(409, "PPO checkpoint is not ready")
    if _hash_file(path) != digest.lower():
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


def validate_agent_release_for_publish(release: AgentRelease) -> None:
    """Validate the complete, deployable contract before freezing its hash."""

    _validate_release_recipe(release)
    _validate_release_environment(release)
    _validate_release_compatibility(release)
    _validate_release_checkpoint(release)


def _audit(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
        )
    )


def _public_visibility(model: type[AgentRelease] | type[BehaviorDataset] | type[PopulationPack]):
    return and_(model.visibility == "public", model.status.in_(PUBLIC_STATUSES))


def _visible_predicate(
    model: type[AgentRelease] | type[BehaviorDataset] | type[PopulationPack],
    user: User | None,
):
    if _is_admin(user):
        return model.id.is_not(None)
    public = _public_visibility(model)
    if user is None:
        return public
    return or_(public, model.owner_id == user.id)


def can_view_artifact(
    row: AgentRelease | BehaviorDataset | PopulationPack, user: User | None
) -> bool:
    if _is_admin(user) or (user is not None and row.owner_id == user.id):
        return True
    return row.visibility == "public" and row.status in PUBLIC_STATUSES


def require_owner(row: AgentRelease | BehaviorDataset, user: User) -> None:
    if row.owner_id != user.id and not _is_admin(user):
        raise EcosystemError(403, "artifact is not yours")


async def _ensure_release_version_available(
    session: AsyncSession,
    *,
    owner_id: int,
    name: str,
    version: str,
    exclude_id: int | None = None,
) -> None:
    query = select(AgentRelease.id).where(
        AgentRelease.owner_id == owner_id,
        AgentRelease.name == name,
        AgentRelease.version == version,
    )
    if exclude_id is not None:
        query = query.where(AgentRelease.id != exclude_id)
    if (await session.execute(query.limit(1))).scalar_one_or_none() is not None:
        raise EcosystemError(409, "an agent release with this name and version already exists")


async def _ensure_dataset_version_available(
    session: AsyncSession,
    *,
    owner_id: int,
    name: str,
    version: str,
    exclude_id: int | None = None,
) -> None:
    query = select(BehaviorDataset.id).where(
        BehaviorDataset.owner_id == owner_id,
        BehaviorDataset.name == name,
        BehaviorDataset.version == version,
    )
    if exclude_id is not None:
        query = query.where(BehaviorDataset.id != exclude_id)
    if (await session.execute(query.limit(1))).scalar_one_or_none() is not None:
        raise EcosystemError(409, "a behavior dataset with this name and version already exists")


def release_content(release: AgentRelease) -> dict[str, Any]:
    return {
        "name": release.name,
        "version": release.version,
        "description": release.description,
        "market": release.market,
        "recipe": release.recipe,
        "state": release.state,
        "compatibility": release.compatibility,
        "environment": release.environment,
        "parent_release_id": release.parent_release_id,
    }


async def list_agent_releases(
    session: AsyncSession,
    user: User | None,
    *,
    market: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AgentRelease]:
    query = select(AgentRelease).where(_visible_predicate(AgentRelease, user))
    if market is not None:
        query = query.where(AgentRelease.market == market)
    query = query.order_by(AgentRelease.created_at.desc(), AgentRelease.id.desc())
    return list((await session.execute(query.offset(offset).limit(limit))).scalars())


async def get_agent_release(
    session: AsyncSession, release_id: int, user: User | None
) -> AgentRelease:
    release = await session.get(AgentRelease, release_id)
    if release is None or not can_view_artifact(release, user):
        raise EcosystemError(404, "agent release not found")
    return release


async def create_agent_release(
    session: AsyncSession, user: User, data: dict[str, Any]
) -> AgentRelease:
    reject_embedded_secrets(data)
    _validate_release_badges(data.get("badges", []), user)
    await _ensure_release_version_available(
        session, owner_id=user.id, name=data["name"], version=data["version"]
    )
    release = AgentRelease(owner_id=user.id, status="draft", **data)
    session.add(release)
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="agent_release.created",
        entity_type="agent_release",
        entity_id=release.id,
    )
    return release


async def update_agent_release(
    session: AsyncSession,
    release_id: int,
    user: User,
    changes: dict[str, Any],
) -> AgentRelease:
    release = await session.get(AgentRelease, release_id)
    if release is None:
        raise EcosystemError(404, "agent release not found")
    require_owner(release, user)
    if release.status != "draft":
        raise EcosystemError(409, "published agent releases are immutable")
    reject_embedded_secrets(changes)
    if "badges" in changes:
        _validate_release_badges(changes["badges"], user)
    next_name = changes.get("name", release.name)
    next_version = changes.get("version", release.version)
    await _ensure_release_version_available(
        session,
        owner_id=release.owner_id,
        name=next_name,
        version=next_version,
        exclude_id=release.id,
    )
    for key, value in changes.items():
        setattr(release, key, value)
    release.updated_at = datetime.now(UTC)
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="agent_release.updated",
        entity_type="agent_release",
        entity_id=release.id,
        payload={"fields": sorted(changes)},
    )
    return release


async def publish_agent_release(session: AsyncSession, release_id: int, user: User) -> AgentRelease:
    release = await session.get(AgentRelease, release_id)
    if release is None:
        raise EcosystemError(404, "agent release not found")
    require_owner(release, user)
    if release.status != "draft":
        raise EcosystemError(409, "agent release is already immutable")
    reject_embedded_secrets(release_content(release), path="release")
    validate_agent_release_for_publish(release)
    release.content_sha256 = _canonical_sha256(release_content(release))
    release.status = "published"
    release.published_at = datetime.now(UTC)
    release.updated_at = release.published_at
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="agent_release.published",
        entity_type="agent_release",
        entity_id=release.id,
        payload={"content_sha256": release.content_sha256},
    )
    return release


async def fork_agent_release(
    session: AsyncSession,
    release_id: int,
    user: User,
    overrides: dict[str, Any],
) -> AgentRelease:
    source = await get_agent_release(session, release_id, user)
    name = overrides.get("name") or f"{source.name} Fork"
    version = overrides.get("version") or "0.1.0"
    await _ensure_release_version_available(session, owner_id=user.id, name=name, version=version)
    fork = AgentRelease(
        owner_id=user.id,
        name=name,
        version=version,
        description=source.description,
        market=source.market,
        visibility=overrides.get("visibility", "private"),
        status="draft",
        recipe=deepcopy(source.recipe),
        state=deepcopy(source.state),
        compatibility=deepcopy(source.compatibility),
        environment=deepcopy(source.environment),
        # Evidence belongs to the exact immutable release hash. A fork starts with
        # no platform-derived badges until it is evaluated independently.
        badges=[badge for badge in deepcopy(source.badges) if badge not in _PLATFORM_BADGES],
        parent_release_id=source.id,
    )
    session.add(fork)
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="agent_release.forked",
        entity_type="agent_release",
        entity_id=fork.id,
        payload={"parent_release_id": source.id},
    )
    return fork


async def list_release_evaluations(
    session: AsyncSession,
    release_id: int,
    user: User | None,
) -> list[ReleaseEvaluation]:
    await get_agent_release(session, release_id, user)
    query = (
        select(ReleaseEvaluation)
        .where(ReleaseEvaluation.release_id == release_id)
        .order_by(ReleaseEvaluation.created_at.desc(), ReleaseEvaluation.id.desc())
    )
    return list((await session.execute(query)).scalars())


async def create_release_evaluation(
    session: AsyncSession,
    release_id: int,
    user: User,
    data: dict[str, Any],
) -> ReleaseEvaluation:
    release = await get_agent_release(session, release_id, user)
    require_owner(release, user)
    if release.status not in PUBLIC_STATUSES or not release.content_sha256:
        raise EcosystemError(409, "publish the immutable release before requesting evaluation")
    reject_embedded_secrets(data)
    kind = data["kind"]
    if release.market == "p2p" and kind in ("deterministic_replay", "fresh_llm_replay"):
        raise EcosystemError(
            422, "P2P releases require a closed-loop tournament or live evaluation"
        )
    if kind == "p2p_tournament" and release.market not in ("p2p", "hybrid"):
        raise EcosystemError(422, "P2P tournaments require a P2P or hybrid release")
    if kind == "hybrid_evaluation" and release.market != "hybrid":
        raise EcosystemError(422, "hybrid evaluation requires a hybrid release")
    evaluation = ReleaseEvaluation(
        release_id=release.id,
        requested_by_id=user.id,
        status="queued",
        provenance="platform_verified",
        **data,
    )
    session.add(evaluation)
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="release_evaluation.queued",
        entity_type="release_evaluation",
        entity_id=evaluation.id,
        payload={"release_id": release.id, "kind": evaluation.kind},
    )
    return evaluation


def _resolve_dataset_artifact_value(artifact_path: str | None, *, must_exist: bool = True) -> Path:
    if not artifact_path:
        raise EcosystemError(409, "dataset artifact is not ready")
    relative = Path(artifact_path)
    if relative.is_absolute():
        raise EcosystemError(422, "dataset artifact path must be relative")
    try:
        base = DATASET_ARTIFACTS_BASE.resolve()
        path = (base / relative).resolve()
    except (OSError, ValueError) as exc:
        raise EcosystemError(422, "invalid dataset artifact path") from exc
    if not path.is_relative_to(base):
        raise EcosystemError(422, "dataset artifact path escapes the artifact directory")
    if must_exist and not path.is_file():
        raise EcosystemError(409, "dataset artifact is not ready")
    return path


def dataset_artifact_path(dataset: BehaviorDataset, *, must_exist: bool = True) -> Path:
    """Resolve a stored relative artifact path with an independent containment check."""

    return _resolve_dataset_artifact_value(dataset.artifact_path, must_exist=must_exist)


def dataset_content(dataset: BehaviorDataset) -> dict[str, Any]:
    return {
        "name": dataset.name,
        "version": dataset.version,
        "description": dataset.description,
        "market": dataset.market,
        "schema_version": dataset.schema_version,
        "manifest": dataset.manifest,
        "artifact_sha256": dataset.artifact_sha256,
        "size_bytes": dataset.size_bytes,
        "row_count": dataset.row_count,
        "license": dataset.license,
        "parent_dataset_id": dataset.parent_dataset_id,
        "source_release_id": dataset.source_release_id,
    }


def _manifest_provenance(manifest: dict[str, Any]) -> str | None:
    raw = manifest.get("provenance")
    if isinstance(raw, str):
        return raw.strip().lower()
    if isinstance(raw, dict):
        value = raw.get("type") or raw.get("kind")
        return value.strip().lower() if isinstance(value, str) else None
    return None


def _validate_user_dataset_manifest(manifest: dict[str, Any]) -> None:
    """Keep verified provenance on trusted service paths, never user assertions."""

    provenance = _manifest_provenance(manifest)
    if provenance in {"platform_verified", "externally_attested"}:
        raise EcosystemError(422, f"{provenance} provenance cannot be self-assigned")
    if any(str(key).startswith("_platform_") for key in manifest):
        raise EcosystemError(422, "platform provenance markers cannot be self-assigned")


async def export_market_session_dataset(
    session: AsyncSession,
    market_session_id: int,
    user: User,
    data: dict[str, Any],
) -> BehaviorDataset:
    """Export the caller's persisted market audit rows as a canonical gzip dataset."""

    market_session = await session.get(MarketSession, market_session_id)
    if market_session is None:
        raise EcosystemError(404, "market session not found")
    await _ensure_dataset_version_available(
        session, owner_id=user.id, name=data["name"], version=data["version"]
    )

    snapshots = list(
        (
            await session.execute(
                select(
                    VppStatSnapshot.vpp_id,
                    VppStatSnapshot.release_id,
                    VppStatSnapshot.release_content_sha256,
                ).where(
                    VppStatSnapshot.session_id == market_session_id,
                    VppStatSnapshot.owner_id == user.id,
                )
            )
        ).all()
    )
    owned_participants = {int(row.vpp_id) for row in snapshots}
    requested = data.get("participant_ids")
    participant_ids = (
        sorted(owned_participants)
        if requested is None
        else sorted({int(participant_id) for participant_id in requested})
    )
    if not participant_ids:
        raise EcosystemError(
            409,
            "no owned participant trajectory is available in this market session",
        )
    unauthorized = sorted(set(participant_ids) - owned_participants)
    if unauthorized:
        raise EcosystemError(
            403,
            "market session participants are not owned by the caller: "
            + ", ".join(str(participant_id) for participant_id in unauthorized),
        )

    selected_snapshots = [row for row in snapshots if int(row.vpp_id) in participant_ids]
    release_ids = sorted(
        {int(row.release_id) for row in selected_snapshots if row.release_id is not None}
    )
    source_release_id = data.get("source_release_id")
    if source_release_id is not None:
        if int(source_release_id) not in release_ids:
            raise EcosystemError(
                422,
                "source_release_id is not bound to the selected session participants",
            )
        source = await session.get(AgentRelease, int(source_release_id))
        if source is None:
            raise EcosystemError(404, "source agent release not found")
        require_owner(source, user)
    elif len(release_ids) == 1:
        source_release_id = release_ids[0]

    events = list(
        (
            await session.execute(
                select(MarketAuditEvent)
                .where(
                    MarketAuditEvent.session_id == market_session_id,
                    MarketAuditEvent.participant_id.in_(participant_ids),
                    MarketAuditEvent.kind.in_(
                        (
                            "decision.received",
                            "gateway.accepted",
                            "gateway.rejected",
                            "delivery.settled",
                        )
                    ),
                )
                .order_by(MarketAuditEvent.sequence_no)
            )
        ).scalars()
    )
    try:
        rows = build_trajectory_rows(events)
    except ValueError as exc:
        raise EcosystemError(422, f"market session trajectory is incomplete: {exc}") from exc
    if not rows:
        raise EcosystemError(409, "market session contains no persisted decision trajectory")

    manifest = {
        "provenance": "platform_verified",
        "generated_by": {
            "service": "eflux-market-audit-export",
            "market_session_id": market_session_id,
        },
        "population": {
            "participant_ids": participant_ids,
            "release_ids": release_ids,
            "release_content_sha256": sorted(
                {
                    str(row.release_content_sha256)
                    for row in selected_snapshots
                    if row.release_content_sha256
                }
            ),
        },
    }
    dataset = BehaviorDataset(
        owner_id=user.id,
        name=data["name"],
        version=data["version"],
        description=data.get("description", ""),
        market=market_session.market_mode,
        visibility=data.get("visibility", "private"),
        status="draft",
        schema_version=DATASET_SCHEMA_VERSION,
        manifest=manifest,
        license=data.get("license", "EFlux-Research-1.0"),
        source_release_id=source_release_id,
    )
    session.add(dataset)
    await session.flush()
    relative = Path(str(user.id)) / str(dataset.id) / "decision-trajectory-v1.jsonl.gz"
    target = _resolve_dataset_artifact_value(relative.as_posix(), must_exist=False)
    try:
        export_trajectory_jsonl_gz(rows, target)
        inspection = inspect_trajectory_artifact(target)
    except (OSError, ValueError) as exc:
        target.unlink(missing_ok=True)
        raise EcosystemError(422, f"market session trajectory is incomplete: {exc}") from exc
    dataset.artifact_path = relative.as_posix()
    dataset.artifact_sha256 = _hash_file(target)
    dataset.size_bytes = target.stat().st_size
    dataset.row_count = int(inspection["row_count"])
    dataset.manifest = {
        **manifest,
        "completeness": inspection["completeness"],
        "observed": inspection["observed"],
        "redaction": inspection["redaction"],
    }
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="behavior_dataset.exported",
        entity_type="behavior_dataset",
        entity_id=dataset.id,
        payload={
            "market_session_id": market_session_id,
            "participant_ids": participant_ids,
            "artifact_sha256": dataset.artifact_sha256,
        },
    )
    # The API sessionmaker disables autoflush; make the trusted provenance record
    # queryable immediately if the owner publishes in the same transaction.
    await session.flush()
    return dataset


async def list_behavior_datasets(
    session: AsyncSession,
    user: User | None,
    *,
    market: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[BehaviorDataset]:
    query = select(BehaviorDataset).where(_visible_predicate(BehaviorDataset, user))
    if market is not None:
        query = query.where(BehaviorDataset.market == market)
    query = query.order_by(BehaviorDataset.created_at.desc(), BehaviorDataset.id.desc())
    return list((await session.execute(query.offset(offset).limit(limit))).scalars())


async def get_behavior_dataset(
    session: AsyncSession, dataset_id: int, user: User | None
) -> BehaviorDataset:
    dataset = await session.get(BehaviorDataset, dataset_id)
    if dataset is None or not can_view_artifact(dataset, user):
        raise EcosystemError(404, "behavior dataset not found")
    return dataset


async def create_behavior_dataset(
    session: AsyncSession, user: User, data: dict[str, Any]
) -> BehaviorDataset:
    reject_embedded_secrets(data)
    manifest = deepcopy(data.get("manifest") or {})
    _validate_user_dataset_manifest(manifest)
    manifest.setdefault("provenance", "self_reported")
    data["manifest"] = manifest
    await _ensure_dataset_version_available(
        session, owner_id=user.id, name=data["name"], version=data["version"]
    )
    if data.get("artifact_path"):
        _resolve_dataset_artifact_value(data["artifact_path"], must_exist=False)
    dataset = BehaviorDataset(owner_id=user.id, status="draft", **data)
    session.add(dataset)
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="behavior_dataset.created",
        entity_type="behavior_dataset",
        entity_id=dataset.id,
    )
    return dataset


async def update_behavior_dataset(
    session: AsyncSession,
    dataset_id: int,
    user: User,
    changes: dict[str, Any],
) -> BehaviorDataset:
    dataset = await session.get(BehaviorDataset, dataset_id)
    if dataset is None:
        raise EcosystemError(404, "behavior dataset not found")
    require_owner(dataset, user)
    if dataset.status != "draft":
        raise EcosystemError(409, "published behavior datasets are immutable")
    reject_embedded_secrets(changes)
    if "manifest" in changes:
        _validate_user_dataset_manifest(changes["manifest"])
    next_name = changes.get("name", dataset.name)
    next_version = changes.get("version", dataset.version)
    await _ensure_dataset_version_available(
        session,
        owner_id=dataset.owner_id,
        name=next_name,
        version=next_version,
        exclude_id=dataset.id,
    )
    if changes.get("artifact_path"):
        _resolve_dataset_artifact_value(changes["artifact_path"], must_exist=False)
    for key, value in changes.items():
        setattr(dataset, key, value)
    dataset.updated_at = datetime.now(UTC)
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="behavior_dataset.updated",
        entity_type="behavior_dataset",
        entity_id=dataset.id,
        payload={"fields": sorted(changes)},
    )
    return dataset


async def get_dataset_attestation_payload(
    session: AsyncSession,
    dataset_id: int,
    user: User,
    *,
    provider_id: str,
    issued_at: datetime,
) -> tuple[BehaviorDataset, dict[str, Any], str]:
    """Prepare the immutable bytes a configured external provider must sign."""

    dataset = await session.get(BehaviorDataset, dataset_id)
    if dataset is None:
        raise EcosystemError(404, "behavior dataset not found")
    require_owner(dataset, user)
    if dataset.status != "draft":
        raise EcosystemError(409, "published behavior datasets are immutable")
    if _manifest_provenance(dataset.manifest) != "self_reported":
        raise EcosystemError(409, "only a self-reported draft can be externally attested")
    provider_id = provider_id.strip()
    if not provider_id:
        raise EcosystemError(422, "provider_id is required")
    if issued_at.tzinfo is None:
        raise EcosystemError(422, "issued_at must include a timezone")
    if issued_at.astimezone(UTC) > datetime.now(UTC) + timedelta(minutes=5):
        raise EcosystemError(422, "issued_at cannot be more than five minutes in the future")
    path = dataset_artifact_path(dataset)
    actual_sha256 = _hash_file(path)
    if dataset.artifact_sha256 and dataset.artifact_sha256 != actual_sha256:
        raise EcosystemError(409, "dataset artifact changed after registration")
    payload = dataset_attestation_payload(
        dataset,
        provider_id=provider_id,
        issued_at=issued_at,
        artifact_sha256=actual_sha256,
    )
    canonical = canonical_attestation_payload(payload)
    return dataset, payload, canonical


async def attest_behavior_dataset(
    session: AsyncSession,
    dataset_id: int,
    user: User,
    *,
    provider_id: str,
    issued_at: datetime,
    signature_sha256: str,
) -> BehaviorDataset:
    """Verify a trusted provider HMAC before assigning external provenance."""

    dataset, payload, canonical = await get_dataset_attestation_payload(
        session,
        dataset_id,
        user,
        provider_id=provider_id,
        issued_at=issued_at,
    )
    configured = get_settings().external_attestation_keys
    secret = configured.get(provider_id)
    if not isinstance(secret, str) or not secret:
        raise EcosystemError(422, "external attestation provider is not trusted")
    expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_sha256.lower()):
        raise EcosystemError(422, "external attestation signature is invalid")

    manifest = deepcopy(dataset.manifest)
    manifest["provenance"] = "externally_attested"
    manifest["external_attestation"] = {
        "provider_id": provider_id,
        "issued_at": payload["issued_at"],
        "verified_at": _utc_iso(datetime.now(UTC)),
        "algorithm": "hmac-sha256",
        "payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "artifact_sha256": payload["dataset"]["artifact_sha256"],
    }
    dataset.manifest = manifest
    dataset.artifact_sha256 = payload["dataset"]["artifact_sha256"]
    dataset.updated_at = datetime.now(UTC)
    _audit(
        session,
        actor_user_id=user.id,
        action="behavior_dataset.externally_attested",
        entity_type="behavior_dataset",
        entity_id=dataset.id,
        payload={
            "provider_id": provider_id,
            "payload_sha256": manifest["external_attestation"]["payload_sha256"],
            "artifact_sha256": dataset.artifact_sha256,
        },
    )
    await session.flush()
    return dataset


async def _validate_dataset_provenance(
    session: AsyncSession, dataset: BehaviorDataset, path: Path
) -> None:
    if _manifest_provenance(dataset.manifest) != "platform_verified":
        return
    trusted_export = (
        await session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.action == "behavior_dataset.exported",
                AuditEvent.entity_type == "behavior_dataset",
                AuditEvent.entity_id == dataset.id,
                AuditEvent.actor_user_id == dataset.owner_id,
            )
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if trusted_export is None:
        raise EcosystemError(422, "platform_verified provenance has no trusted export record")
    expected_sha256 = str(trusted_export.payload.get("artifact_sha256") or "")
    if not expected_sha256 or _hash_file(path) != expected_sha256:
        raise EcosystemError(
            409,
            "platform-generated dataset artifact no longer matches its trusted export digest",
        )


async def publish_behavior_dataset(
    session: AsyncSession, dataset_id: int, user: User
) -> BehaviorDataset:
    dataset = await session.get(BehaviorDataset, dataset_id)
    if dataset is None:
        raise EcosystemError(404, "behavior dataset not found")
    require_owner(dataset, user)
    if dataset.status != "draft":
        raise EcosystemError(409, "behavior dataset is already immutable")
    path = dataset_artifact_path(dataset)
    await _validate_dataset_provenance(session, dataset, path)
    try:
        inspection = inspect_trajectory_artifact(path)
    except ValueError as exc:
        raise EcosystemError(422, f"Decision Trajectory validation failed: {exc}") from exc
    if dataset.schema_version != inspection["schema_version"]:
        raise EcosystemError(
            422,
            "dataset schema_version does not match the trajectory artifact",
        )
    if dataset.market in ("p2p", "hybrid") and not dataset.manifest.get("population"):
        raise EcosystemError(422, "P2P and hybrid datasets must identify their population")
    manifest = deepcopy(dataset.manifest)
    manifest["completeness"] = inspection["completeness"]
    manifest["observed"] = inspection["observed"]
    manifest["redaction"] = inspection["redaction"]
    manifest["inspection"] = {
        "validator": "eflux-decision-trajectory-v1",
    }
    dataset.manifest = manifest
    dataset.artifact_path = path.relative_to(DATASET_ARTIFACTS_BASE.resolve()).as_posix()
    dataset.artifact_sha256 = _hash_file(path)
    dataset.size_bytes = path.stat().st_size
    dataset.row_count = int(inspection["row_count"])
    dataset.content_sha256 = _canonical_sha256(dataset_content(dataset))
    dataset.status = "published"
    dataset.published_at = datetime.now(UTC)
    dataset.updated_at = dataset.published_at
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="behavior_dataset.published",
        entity_type="behavior_dataset",
        entity_id=dataset.id,
        payload={
            "content_sha256": dataset.content_sha256,
            "artifact_sha256": dataset.artifact_sha256,
        },
    )
    return dataset


async def get_dataset_download(
    session: AsyncSession, dataset_id: int, user: User | None
) -> tuple[BehaviorDataset, Path]:
    dataset = await get_behavior_dataset(session, dataset_id, user)
    path = dataset_artifact_path(dataset)
    if not dataset.artifact_sha256:
        raise EcosystemError(409, "dataset artifact has no trusted digest; publish it first")
    if _hash_file(path) != dataset.artifact_sha256:
        raise EcosystemError(409, "dataset artifact no longer matches its published digest")
    return dataset, path


async def create_dataset_training_run(
    session: AsyncSession,
    dataset_id: int,
    user: User,
    data: dict[str, Any],
) -> DatasetTrainingRun:
    dataset = await get_behavior_dataset(session, dataset_id, user)
    reject_embedded_secrets(data)
    dataset_artifact_path(dataset)
    if data["algorithm"] == "ppo_finetune" and not (
        data.get("config", {}).get("warm_start_release_id")
        or data.get("config", {}).get("warm_start_training_run_id")
    ):
        raise EcosystemError(
            422,
            "PPO fine-tuning requires a BC warm-start release or training run",
        )
    run = DatasetTrainingRun(
        dataset_id=dataset.id,
        owner_id=user.id,
        status="queued",
        **data,
    )
    session.add(run)
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="dataset_training.queued",
        entity_type="dataset_training_run",
        entity_id=run.id,
        payload={"dataset_id": dataset.id, "algorithm": run.algorithm},
    )
    return run


async def get_dataset_training_run(
    session: AsyncSession, run_id: int, user: User
) -> DatasetTrainingRun:
    run = await session.get(DatasetTrainingRun, run_id)
    if run is None:
        raise EcosystemError(404, "training run not found")
    if run.owner_id != user.id and not _is_admin(user):
        raise EcosystemError(404, "training run not found")
    return run


def population_content(pack: PopulationPack) -> dict[str, Any]:
    return {
        "name": pack.name,
        "version": pack.version,
        "description": pack.description,
        "spec": pack.spec,
    }


async def list_population_packs(
    session: AsyncSession,
    user: User | None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[PopulationPack]:
    query = (
        select(PopulationPack)
        .where(_visible_predicate(PopulationPack, user))
        .order_by(PopulationPack.created_at.desc(), PopulationPack.id.desc())
    )
    return list((await session.execute(query.offset(offset).limit(limit))).scalars())


async def create_population_pack(
    session: AsyncSession, user: User, data: dict[str, Any]
) -> PopulationPack:
    if not _is_admin(user):
        raise EcosystemError(403, "admin privileges required to publish a population pack")
    reject_embedded_secrets(data)
    existing = (
        await session.execute(
            select(PopulationPack.id).where(
                PopulationPack.owner_id == user.id,
                PopulationPack.name == data["name"],
                PopulationPack.version == data["version"],
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise EcosystemError(409, "a population pack with this name and version already exists")
    pack = PopulationPack(
        owner_id=user.id,
        status="published",
        published_at=datetime.now(UTC),
        **data,
    )
    pack.content_sha256 = _canonical_sha256(population_content(pack))
    session.add(pack)
    await session.flush()
    _audit(
        session,
        actor_user_id=user.id,
        action="population_pack.published",
        entity_type="population_pack",
        entity_id=pack.id,
        payload={"content_sha256": pack.content_sha256},
    )
    return pack
