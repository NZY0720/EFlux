"""REST API for immutable agent releases and behavior-learning artifacts."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eflux.api.deps import CurrentUser, DbSession, SimulatorDep
from eflux.auth.api_key import verify_api_key
from eflux.auth.session import get_user_for_session_token
from eflux.config import get_settings
from eflux.db.models import (
    VPP,
    AgentRelease,
    AuditEvent,
    BehaviorDataset,
    DatasetTrainingRun,
    PopulationPack,
    ReleaseEvaluation,
    User,
)
from eflux.ecosystem import service
from eflux.ecosystem.catalog import get_standard_profile, list_standard_profiles
from eflux.ecosystem.deployment import (
    InvalidDeployment,
    UnsafeLiveDeployment,
    assert_deployment_compatibility,
    assert_live_risk_contract,
    normalize_credential_bindings,
)
from eflux.ecosystem.runtime import agent_factory_from_release
from eflux.ecosystem.runtime_identity import repository_git_commit
from eflux.simulator.agent_spec import validate_vpp_params
from eflux.simulator.scenarios import provision_managed_vpp

router = APIRouter(tags=["ecosystem"])

Market = Literal["realprice", "p2p", "hybrid"]
Visibility = Literal["public", "private"]
EvaluationKind = Literal[
    "deterministic_replay",
    "fresh_llm_replay",
    "forward_shadow",
    "verified_live",
    "p2p_tournament",
    "hybrid_evaluation",
]
Provenance = Literal["platform_verified", "externally_attested", "self_reported"]
TrainingAlgorithm = Literal["bc_warm_start", "ppo_finetune"]


async def optional_current_user(
    session: DbSession,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """Resolve credentials when supplied, while leaving public reads anonymous."""

    if authorization:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization.split(" ", 1)[1].strip()
        user = await verify_api_key(session, token)
        if user is None:
            user = await get_user_for_session_token(session, token)
        if user is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    token = request.cookies.get("eflux_session")
    if token:
        user = await get_user_for_session_token(session, token)
        if user is not None:
            return user
    return None


OptionalUser = Annotated[User | None, Depends(optional_current_user)]


async def _service_call[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except service.EcosystemError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


class AgentReleaseCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=10_000)
    market: Market
    visibility: Visibility = "private"
    recipe: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    badges: list[str] = Field(default_factory=list, max_length=32)


class AgentReleasePatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    version: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=10_000)
    market: Market | None = None
    visibility: Visibility | None = None
    recipe: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    compatibility: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    badges: list[str] | None = Field(default=None, max_length=32)


class AgentReleaseForkIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    version: str | None = Field(default=None, min_length=1, max_length=64)
    visibility: Visibility = "private"


class AgentReleaseDeployIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    profile_id: str = Field(default="battery-only", min_length=1, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["shadow", "paper", "live"] = "shadow"
    risk_acknowledged: bool = False
    credential_bindings: list[str] = Field(default_factory=list, max_length=32)


class AgentReleaseDeploymentOut(BaseModel):
    id: int
    vpp_id: int
    release_id: int
    release_content_sha256: str
    name: str
    mode: Literal["shadow", "paper", "live"]
    params: dict[str, Any]


class AgentReleasePromoteLiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_acknowledged: bool


class AgentReleaseOut(BaseModel):
    id: int
    owner_id: int
    name: str
    version: str
    description: str
    market: Market
    visibility: Visibility
    status: Literal["draft", "published", "verified"]
    recipe: dict[str, Any]
    state: dict[str, Any]
    compatibility: dict[str, Any]
    environment: dict[str, Any]
    badges: list[str]
    parent_release_id: int | None
    content_sha256: str | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


def _release_out(row: AgentRelease) -> AgentReleaseOut:
    return AgentReleaseOut(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        version=row.version,
        description=row.description,
        market=row.market,
        visibility=row.visibility,
        status=row.status,
        recipe=row.recipe,
        state=row.state,
        compatibility=row.compatibility,
        environment=row.environment,
        badges=row.badges,
        parent_release_id=row.parent_release_id,
        content_sha256=row.content_sha256,
        created_at=row.created_at,
        updated_at=row.updated_at,
        published_at=row.published_at,
    )


class ReleaseEvaluationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EvaluationKind
    config: dict[str, Any] = Field(default_factory=dict)


class ReleaseEvaluationOut(BaseModel):
    id: int
    release_id: int
    requested_by_id: int
    kind: EvaluationKind
    status: Literal["queued", "running", "done", "failed"]
    provenance: Provenance
    config: dict[str, Any]
    metrics: dict[str, Any]
    evidence: dict[str, Any] | None
    evidence_sha256: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _evaluation_out(row: ReleaseEvaluation) -> ReleaseEvaluationOut:
    return ReleaseEvaluationOut(
        id=row.id,
        release_id=row.release_id,
        requested_by_id=row.requested_by_id,
        kind=row.kind,
        status=row.status,
        provenance=row.provenance,
        config=row.config,
        metrics=row.metrics,
        evidence=row.evidence,
        evidence_sha256=row.evidence_sha256,
        error=row.error,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


class BehaviorDatasetCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=10_000)
    market: Market
    visibility: Visibility = "private"
    schema_version: Literal["1"] = "1"
    manifest: dict[str, Any] = Field(default_factory=dict)
    artifact_path: str | None = Field(default=None, max_length=500)
    row_count: int = Field(default=0, ge=0)
    license: str = Field(default="EFlux-Research-1.0", min_length=1, max_length=100)
    parent_dataset_id: int | None = Field(default=None, gt=0)
    source_release_id: int | None = Field(default=None, gt=0)


class BehaviorDatasetPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    version: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=10_000)
    market: Market | None = None
    visibility: Visibility | None = None
    schema_version: Literal["1"] | None = None
    manifest: dict[str, Any] | None = None
    artifact_path: str | None = Field(default=None, max_length=500)
    row_count: int | None = Field(default=None, ge=0)
    license: str | None = Field(default=None, min_length=1, max_length=100)
    parent_dataset_id: int | None = Field(default=None, gt=0)
    source_release_id: int | None = Field(default=None, gt=0)


class BehaviorDatasetAttestationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    issued_at: datetime
    signature_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class BehaviorDatasetAttestationPayloadOut(BaseModel):
    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    payload: dict[str, Any]
    canonical_payload: str
    payload_sha256: str


class BehaviorDatasetExportIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=10_000)
    visibility: Visibility = "private"
    participant_ids: list[int] | None = Field(default=None, max_length=100)
    source_release_id: int | None = Field(default=None, gt=0)
    license: str = Field(default="EFlux-Research-1.0", min_length=1, max_length=100)


class BehaviorDatasetOut(BaseModel):
    id: int
    owner_id: int
    name: str
    version: str
    description: str
    market: Market
    visibility: Visibility
    status: Literal["draft", "published", "verified"]
    schema_version: str
    manifest: dict[str, Any]
    artifact_sha256: str | None
    size_bytes: int
    row_count: int
    license: str
    parent_dataset_id: int | None
    source_release_id: int | None
    content_sha256: str | None
    download_available: bool
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


def _dataset_out(row: BehaviorDataset) -> BehaviorDatasetOut:
    return BehaviorDatasetOut(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        version=row.version,
        description=row.description,
        market=row.market,
        visibility=row.visibility,
        status=row.status,
        schema_version=row.schema_version,
        manifest=row.manifest,
        artifact_sha256=row.artifact_sha256,
        size_bytes=row.size_bytes,
        row_count=row.row_count,
        license=row.license,
        parent_dataset_id=row.parent_dataset_id,
        source_release_id=row.source_release_id,
        content_sha256=row.content_sha256,
        download_available=bool(row.artifact_path),
        created_at=row.created_at,
        updated_at=row.updated_at,
        published_at=row.published_at,
    )


class DatasetTrainIn(BaseModel):
    algorithm: TrainingAlgorithm
    config: dict[str, Any] = Field(default_factory=dict)


class DatasetTrainingRunOut(BaseModel):
    id: int
    dataset_id: int
    owner_id: int
    algorithm: TrainingAlgorithm
    status: Literal["queued", "running", "succeeded", "failed"]
    config: dict[str, Any]
    metrics: dict[str, Any]
    output_release_id: int | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _training_out(row: DatasetTrainingRun) -> DatasetTrainingRunOut:
    return DatasetTrainingRunOut(
        id=row.id,
        dataset_id=row.dataset_id,
        owner_id=row.owner_id,
        algorithm=row.algorithm,
        status=row.status,
        config=row.config,
        metrics=row.metrics,
        output_release_id=row.output_release_id,
        error=row.error,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


class PopulationPackCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=10_000)
    visibility: Visibility = "public"
    spec: dict[str, Any] = Field(default_factory=dict)


class PopulationPackOut(BaseModel):
    id: int
    owner_id: int | None
    name: str
    version: str
    description: str
    visibility: Visibility
    status: Literal["draft", "published", "verified"]
    spec: dict[str, Any]
    content_sha256: str | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class StandardAssetProfileOut(BaseModel):
    id: str
    version: str
    name: str
    description: str
    market: Literal["realprice"]
    spec: dict[str, Any]
    content_sha256: str


class PlatformRuntimeIdentityOut(BaseModel):
    git_commit: str | None
    configured_by: Literal["EFLUX_GIT_COMMIT", "repository", "unavailable"]


def _population_out(row: PopulationPack) -> PopulationPackOut:
    return PopulationPackOut(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        version=row.version,
        description=row.description,
        visibility=row.visibility,
        status=row.status,
        spec=row.spec,
        content_sha256=row.content_sha256,
        created_at=row.created_at,
        updated_at=row.updated_at,
        published_at=row.published_at,
    )


@router.get("/agent-releases", response_model=list[AgentReleaseOut])
async def list_agent_releases(
    session: DbSession,
    user: OptionalUser,
    market: Market | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[AgentReleaseOut]:
    rows = await _service_call(
        service.list_agent_releases(session, user, market=market, limit=limit, offset=offset)
    )
    return [_release_out(row) for row in rows]


@router.post(
    "/agent-releases",
    response_model=AgentReleaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_release(
    body: AgentReleaseCreateIn, session: DbSession, user: CurrentUser
) -> AgentReleaseOut:
    row = await _service_call(service.create_agent_release(session, user, body.model_dump()))
    return _release_out(row)


@router.get("/agent-releases/{release_id}", response_model=AgentReleaseOut)
async def get_agent_release(
    release_id: int, session: DbSession, user: OptionalUser
) -> AgentReleaseOut:
    return _release_out(await _service_call(service.get_agent_release(session, release_id, user)))


@router.patch("/agent-releases/{release_id}", response_model=AgentReleaseOut)
async def patch_agent_release(
    release_id: int,
    body: AgentReleasePatchIn,
    session: DbSession,
    user: CurrentUser,
) -> AgentReleaseOut:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    row = await _service_call(service.update_agent_release(session, release_id, user, changes))
    return _release_out(row)


@router.post("/agent-releases/{release_id}/publish", response_model=AgentReleaseOut)
async def publish_agent_release(
    release_id: int, session: DbSession, user: CurrentUser
) -> AgentReleaseOut:
    return _release_out(
        await _service_call(service.publish_agent_release(session, release_id, user))
    )


@router.post(
    "/agent-releases/{release_id}/fork",
    response_model=AgentReleaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def fork_agent_release(
    release_id: int,
    body: AgentReleaseForkIn,
    session: DbSession,
    user: CurrentUser,
) -> AgentReleaseOut:
    overrides = body.model_dump(exclude_none=True)
    return _release_out(
        await _service_call(service.fork_agent_release(session, release_id, user, overrides))
    )


async def _platform_evaluation_id(session: AsyncSession, release_id: int) -> int | None:
    return (
        await session.execute(
            select(ReleaseEvaluation.id)
            .where(
                ReleaseEvaluation.release_id == release_id,
                ReleaseEvaluation.status == "done",
                ReleaseEvaluation.provenance == "platform_verified",
            )
            .order_by(ReleaseEvaluation.finished_at.desc(), ReleaseEvaluation.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _deployment_out(row: VPP, runtime: Any) -> AgentReleaseDeploymentOut:
    config = dict(row.managed_config or {})
    if row.release_id is None or row.release_content_sha256 is None:
        raise HTTPException(409, "managed VPP is not bound to an immutable Agent Release")
    return AgentReleaseDeploymentOut(
        id=row.id,
        vpp_id=runtime.vpp_id,
        release_id=row.release_id,
        release_content_sha256=row.release_content_sha256,
        name=row.name,
        mode=config.get("deployment_mode", "live"),
        params=dict(row.params),
    )


@router.post(
    "/agent-releases/{release_id}/deploy",
    response_model=AgentReleaseDeploymentOut,
    status_code=status.HTTP_201_CREATED,
)
async def deploy_agent_release(
    release_id: int,
    body: AgentReleaseDeployIn,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> AgentReleaseDeploymentOut:
    """Create an independently stateful managed deployment bound to one release hash."""

    release = await _service_call(service.get_agent_release(session, release_id, user))
    if release.status not in ("published", "verified") or not release.content_sha256:
        raise HTTPException(409, "publish the immutable release before deployment")
    if release.market != sim.market_mode:
        raise HTTPException(
            422,
            f"release targets {release.market!r}, but this process runs {sim.market_mode!r}",
        )
    evaluation_id: int | None = None
    if body.mode == "live":
        if not body.risk_acknowledged:
            raise HTTPException(422, "live deployment requires explicit risk acknowledgement")
        evaluation_id = await _platform_evaluation_id(session, release.id)
        if evaluation_id is None:
            raise HTTPException(409, "complete a platform evaluation before live deployment")
    if len(sim.my_managed_vpps(user.id)) >= 5:
        raise HTTPException(409, "managed-agent limit reached (5 per account)")
    conflict = (
        await session.execute(select(VPP.id).where(VPP.owner_id == user.id, VPP.name == body.name))
    ).scalar_one_or_none()
    if conflict is not None:
        raise HTTPException(409, "a VPP with this name already exists")
    try:
        profile = get_standard_profile(body.profile_id)
        parsed = validate_vpp_params({**profile["spec"]["vpp_params"], **dict(body.params)})
        agent_factory_from_release(
            release, learning=bool(release.recipe.get("online_learning", False))
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc

    settings = get_settings()
    try:
        assert_deployment_compatibility(
            release,
            profile=profile,
            profile_id=body.profile_id,
            params=parsed,
            available_credit_usd=float(parsed.get("starting_cash_usd", 0.0))
            + float(sim.gateway.limits.credit_limit_usd),
            decision_interval_seconds=settings.agent_decision_interval_sec,
            product_granularity_seconds=settings.delivery_interval_sec,
        )
        if body.mode == "live":
            assert_live_risk_contract(release, sim.gateway.limits)
        credential_bindings = normalize_credential_bindings(
            release, body.credential_bindings
        )
    except InvalidDeployment as exc:
        raise HTTPException(422, str(exc)) from exc
    except UnsafeLiveDeployment as exc:
        raise HTTPException(409, str(exc)) from exc

    raw_algorithm = str(release.recipe.get("algorithm") or "").lower()
    algorithm = "scripted" if raw_algorithm in {"scripted", "strategy"} else raw_algorithm
    llm = release.recipe.get("llm") if isinstance(release.recipe.get("llm"), dict) else {}
    checkpoint = release.state.get("checkpoint_path")
    config = {
        "algorithm": algorithm,
        "llm_enabled": bool(llm),
        "online_learning": bool(release.recipe.get("online_learning", False)),
        "agent_params": dict(release.recipe.get("agent_params") or {}),
        "persona": llm.get("system_prompt"),
        "model": llm.get("model"),
        "checkpoint": checkpoint,
        "deployment_mode": body.mode,
        "profile_id": body.profile_id,
        # Names only: credential values stay outside Release/VPP persistence.
        "credential_bindings": credential_bindings,
        "live_risk_acknowledged_at": (
            datetime.now(UTC).isoformat() if body.mode == "live" else None
        ),
        "live_evaluation_id": evaluation_id,
    }
    row = VPP(
        owner_id=user.id,
        name=body.name,
        params=parsed,
        is_external=True,
        is_managed=True,
        managed_config=config,
        release_id=release.id,
        release_content_sha256=release.content_sha256,
    )
    session.add(row)
    await session.flush()
    try:
        async with sim._lock:
            vpp = provision_managed_vpp(
                sim,
                owner_id=user.id,
                name=row.name,
                params=parsed,
                persona_prompt=config["persona"],
                agent_params=config["agent_params"],
                model=config["model"],
                managed_def_id=row.id,
                release_id=release.id,
                release_content_sha256=release.content_sha256,
                checkpoint=str(checkpoint) if checkpoint else None,
                deployment_mode=body.mode,
                algorithm=algorithm,
                llm_enabled=config["llm_enabled"],
                online_learning=config["online_learning"],
            )
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="agent_release.deployed",
            entity_type="vpp",
            entity_id=row.id,
            payload={
                "release_id": release.id,
                "release_content_sha256": release.content_sha256,
                "deployment_mode": body.mode,
            },
        )
    )
    return _deployment_out(row, vpp)


@router.post(
    "/agent-deployments/{deployment_id}/promote-live",
    response_model=AgentReleaseDeploymentOut,
)
async def promote_agent_deployment_live(
    deployment_id: int,
    body: AgentReleasePromoteLiveIn,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> AgentReleaseDeploymentOut:
    """Manually promote one shadow/paper instance without replacing its runtime state."""

    if not body.risk_acknowledged:
        raise HTTPException(422, "live promotion requires explicit risk acknowledgement")
    row = (
        await session.execute(
            select(VPP).where(
                VPP.id == deployment_id,
                VPP.owner_id == user.id,
                VPP.is_managed.is_(True),
                VPP.release_id.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Agent Release deployment not found")
    config = dict(row.managed_config or {})
    current_mode = config.get("deployment_mode")
    if current_mode not in ("shadow", "paper"):
        raise HTTPException(409, "only a shadow or paper deployment can be promoted")
    release = await session.get(AgentRelease, row.release_id)
    if (
        release is None
        or release.status not in ("published", "verified")
        or not release.content_sha256
        or release.content_sha256 != row.release_content_sha256
    ):
        raise HTTPException(409, "deployment is no longer bound to the exact published release")
    if release.market != sim.market_mode:
        raise HTTPException(422, "release market does not match this market process")
    evaluation_id = await _platform_evaluation_id(session, release.id)
    if evaluation_id is None:
        raise HTTPException(409, "complete a platform evaluation before live promotion")
    profile_id = config.get("profile_id")
    if not isinstance(profile_id, str):
        raise HTTPException(409, "deployment is missing its immutable asset-profile binding")
    try:
        profile = get_standard_profile(profile_id)
        parsed = validate_vpp_params(dict(row.params))
        agent_factory_from_release(
            release, learning=bool(release.recipe.get("online_learning", False))
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    settings = get_settings()
    try:
        assert_deployment_compatibility(
            release,
            profile=profile,
            profile_id=profile_id,
            params=parsed,
            available_credit_usd=float(parsed.get("starting_cash_usd", 0.0))
            + float(sim.gateway.limits.credit_limit_usd),
            decision_interval_seconds=settings.agent_decision_interval_sec,
            product_granularity_seconds=settings.delivery_interval_sec,
        )
        assert_live_risk_contract(release, sim.gateway.limits)
    except InvalidDeployment as exc:
        raise HTTPException(422, str(exc)) from exc
    except UnsafeLiveDeployment as exc:
        raise HTTPException(409, str(exc)) from exc

    runtime = next(
        (vpp for vpp in sim.my_managed_vpps(user.id) if vpp.managed_def_id == row.id),
        None,
    )
    if runtime is None:
        raise HTTPException(409, "deployment runtime is unavailable; restart it before promotion")
    if runtime.release_id != release.id or runtime.release_content_sha256 != release.content_sha256:
        raise HTTPException(409, "runtime release identity does not match the deployment")

    promoted_at = datetime.now(UTC)
    async with sim._lock:
        # Deliberately mutate only the execution mode. Cash, positions, PPO state,
        # LLM memory, logs, and the VPP/agent objects stay independent and intact.
        runtime.deployment_mode = "live"
        row.managed_config = {
            **config,
            "deployment_mode": "live",
            "live_risk_acknowledged_at": promoted_at.isoformat(),
            "live_evaluation_id": evaluation_id,
        }
    session.add(
        AuditEvent(
            actor_user_id=user.id,
            action="agent_release.deployment_promoted_live",
            entity_type="vpp",
            entity_id=row.id,
            payload={
                "release_id": release.id,
                "release_content_sha256": release.content_sha256,
                "from_mode": current_mode,
                "platform_evaluation_id": evaluation_id,
                "risk_acknowledged_at": promoted_at.isoformat(),
            },
        )
    )
    await session.flush()
    return _deployment_out(row, runtime)


@router.get(
    "/agent-releases/{release_id}/evaluations",
    response_model=list[ReleaseEvaluationOut],
)
async def list_release_evaluations(
    release_id: int, session: DbSession, user: OptionalUser
) -> list[ReleaseEvaluationOut]:
    rows = await _service_call(service.list_release_evaluations(session, release_id, user))
    return [_evaluation_out(row) for row in rows]


@router.post(
    "/agent-releases/{release_id}/evaluations",
    response_model=ReleaseEvaluationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_evaluation(
    release_id: int,
    body: ReleaseEvaluationCreateIn,
    session: DbSession,
    user: CurrentUser,
) -> ReleaseEvaluationOut:
    row = await _service_call(
        service.create_release_evaluation(session, release_id, user, body.model_dump())
    )
    return _evaluation_out(row)


@router.get("/behavior-datasets", response_model=list[BehaviorDatasetOut])
async def list_behavior_datasets(
    session: DbSession,
    user: OptionalUser,
    market: Market | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[BehaviorDatasetOut]:
    rows = await _service_call(
        service.list_behavior_datasets(session, user, market=market, limit=limit, offset=offset)
    )
    return [_dataset_out(row) for row in rows]


@router.post(
    "/behavior-datasets",
    response_model=BehaviorDatasetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_behavior_dataset(
    body: BehaviorDatasetCreateIn, session: DbSession, user: CurrentUser
) -> BehaviorDatasetOut:
    row = await _service_call(service.create_behavior_dataset(session, user, body.model_dump()))
    return _dataset_out(row)


@router.put("/behavior-datasets/{dataset_id}/artifact", response_model=BehaviorDatasetOut)
async def upload_behavior_dataset_artifact(
    dataset_id: int,
    request: Request,
    session: DbSession,
    user: CurrentUser,
    artifact_format: Literal["jsonl", "jsonl_gz"] = Query("jsonl_gz"),
) -> BehaviorDatasetOut:
    """Stream one draft JSONL artifact into the platform-owned dataset directory."""

    dataset = await _service_call(service.get_behavior_dataset(session, dataset_id, user))
    service.require_owner(dataset, user)
    if dataset.status != "draft":
        raise HTTPException(409, "published behavior datasets are immutable")
    provenance = service._manifest_provenance(dataset.manifest)
    if provenance in {"platform_verified", "externally_attested"}:
        raise HTTPException(409, f"{provenance} artifacts cannot be replaced")
    maximum = 250 * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > maximum:
                raise HTTPException(413, "dataset artifact exceeds the 250 MiB upload limit")
        except ValueError as exc:
            raise HTTPException(400, "invalid Content-Length") from exc
    suffix = ".jsonl.gz" if artifact_format == "jsonl_gz" else ".jsonl"
    relative = f"{user.id}/{dataset.id}/uploaded{suffix}"
    target = service._resolve_dataset_artifact_value(relative, must_exist=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.uploading")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(413, "dataset artifact exceeds the 250 MiB upload limit")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise HTTPException(422, "dataset artifact is empty")
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    dataset.artifact_path = relative
    dataset.artifact_sha256 = digest.hexdigest()
    dataset.size_bytes = size
    dataset.row_count = 0
    dataset.content_sha256 = None
    service._audit(
        session,
        actor_user_id=user.id,
        action="behavior_dataset.artifact_uploaded",
        entity_type="behavior_dataset",
        entity_id=dataset.id,
        payload={"artifact_sha256": dataset.artifact_sha256, "size_bytes": size},
    )
    await session.flush()
    return _dataset_out(dataset)


@router.get(
    "/behavior-datasets/{dataset_id}/attestation-payload",
    response_model=BehaviorDatasetAttestationPayloadOut,
)
async def get_behavior_dataset_attestation_payload(
    dataset_id: int,
    session: DbSession,
    user: CurrentUser,
    provider_id: Annotated[str, Query(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")],
    issued_at: Annotated[datetime, Query()],
) -> BehaviorDatasetAttestationPayloadOut:
    """Return the exact canonical draft payload a trusted provider must sign."""

    _, payload, canonical = await _service_call(
        service.get_dataset_attestation_payload(
            session,
            dataset_id,
            user,
            provider_id=provider_id,
            issued_at=issued_at,
        )
    )
    return BehaviorDatasetAttestationPayloadOut(
        payload=payload,
        canonical_payload=canonical,
        payload_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
    )


@router.post(
    "/behavior-datasets/{dataset_id}/attest",
    response_model=BehaviorDatasetOut,
)
async def attest_behavior_dataset(
    dataset_id: int,
    body: BehaviorDatasetAttestationIn,
    session: DbSession,
    user: CurrentUser,
) -> BehaviorDatasetOut:
    """Assign external provenance only after a configured provider signature verifies."""

    row = await _service_call(
        service.attest_behavior_dataset(
            session,
            dataset_id,
            user,
            provider_id=body.provider_id,
            issued_at=body.issued_at,
            signature_sha256=body.signature_sha256,
        )
    )
    return _dataset_out(row)


@router.post(
    "/market-sessions/{market_session_id}/behavior-datasets",
    response_model=BehaviorDatasetOut,
    status_code=status.HTTP_201_CREATED,
)
async def export_market_session_behavior_dataset(
    market_session_id: int,
    body: BehaviorDatasetExportIn,
    session: DbSession,
    user: CurrentUser,
) -> BehaviorDatasetOut:
    """Export the caller's persisted audit trajectory as platform-generated gzip JSONL."""

    row = await _service_call(
        service.export_market_session_dataset(
            session,
            market_session_id,
            user,
            body.model_dump(),
        )
    )
    return _dataset_out(row)


@router.get("/behavior-datasets/{dataset_id}", response_model=BehaviorDatasetOut)
async def get_behavior_dataset(
    dataset_id: int, session: DbSession, user: OptionalUser
) -> BehaviorDatasetOut:
    return _dataset_out(
        await _service_call(service.get_behavior_dataset(session, dataset_id, user))
    )


@router.patch("/behavior-datasets/{dataset_id}", response_model=BehaviorDatasetOut)
async def patch_behavior_dataset(
    dataset_id: int,
    body: BehaviorDatasetPatchIn,
    session: DbSession,
    user: CurrentUser,
) -> BehaviorDatasetOut:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    row = await _service_call(service.update_behavior_dataset(session, dataset_id, user, changes))
    return _dataset_out(row)


@router.post("/behavior-datasets/{dataset_id}/publish", response_model=BehaviorDatasetOut)
async def publish_behavior_dataset(
    dataset_id: int, session: DbSession, user: CurrentUser
) -> BehaviorDatasetOut:
    return _dataset_out(
        await _service_call(service.publish_behavior_dataset(session, dataset_id, user))
    )


@router.get("/behavior-datasets/{dataset_id}/download", response_class=FileResponse)
async def download_behavior_dataset(
    dataset_id: int, session: DbSession, user: OptionalUser
) -> FileResponse:
    dataset, path = await _service_call(service.get_dataset_download(session, dataset_id, user))
    suffix = path.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", path.suffix) else ""
    return FileResponse(
        path,
        filename=f"behavior-dataset-{dataset.id}{suffix}",
        media_type="application/octet-stream",
        headers={
            "X-Artifact-SHA256": dataset.artifact_sha256 or "",
            "X-Content-SHA256": dataset.content_sha256 or "",
        },
    )


@router.post(
    "/behavior-datasets/{dataset_id}/train",
    response_model=DatasetTrainingRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def train_behavior_dataset(
    dataset_id: int,
    body: DatasetTrainIn,
    session: DbSession,
    user: CurrentUser,
) -> DatasetTrainingRunOut:
    row = await _service_call(
        service.create_dataset_training_run(session, dataset_id, user, body.model_dump())
    )
    return _training_out(row)


@router.get("/training-runs/{run_id}", response_model=DatasetTrainingRunOut)
async def get_training_run(
    run_id: int, session: DbSession, user: CurrentUser
) -> DatasetTrainingRunOut:
    return _training_out(
        await _service_call(service.get_dataset_training_run(session, run_id, user))
    )


@router.get("/population-packs", response_model=list[PopulationPackOut])
async def list_population_packs(
    session: DbSession,
    user: OptionalUser,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[PopulationPackOut]:
    rows = await _service_call(
        service.list_population_packs(session, user, limit=limit, offset=offset)
    )
    return [_population_out(row) for row in rows]


@router.get("/standard-asset-profiles", response_model=list[StandardAssetProfileOut])
async def standard_asset_profiles() -> list[StandardAssetProfileOut]:
    return [StandardAssetProfileOut(**profile) for profile in list_standard_profiles()]


@router.get("/platform-runtime-identity", response_model=PlatformRuntimeIdentityOut)
def platform_runtime_identity() -> PlatformRuntimeIdentityOut:
    configured = bool(os.environ.get("EFLUX_GIT_COMMIT", "").strip())
    commit = repository_git_commit()
    return PlatformRuntimeIdentityOut(
        git_commit=commit,
        configured_by=(
            "EFLUX_GIT_COMMIT" if configured else ("repository" if commit else "unavailable")
        ),
    )


@router.post(
    "/population-packs",
    response_model=PopulationPackOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_population_pack(
    body: PopulationPackCreateIn, session: DbSession, user: CurrentUser
) -> PopulationPackOut:
    row = await _service_call(service.create_population_pack(session, user, body.model_dump()))
    return _population_out(row)
