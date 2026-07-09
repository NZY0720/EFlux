"""VPP CRUD for authenticated users (manage their owned VPPs)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select

from eflux.agents.reflective.chat import clean_chat_line
from eflux.agents.reflective.pool import CURATED_MODELS
from eflux.agents.reflective.strategist import ExternalStrategist
from eflux.api.deps import CurrentUser, DbSession, SimulatorDep
from eflux.api.ratelimit import RateLimiter
from eflux.db.models import VPP
from eflux.market.units import internal_cash_to_usd
from eflux.simulator.agent_spec import validate_vpp_params
from eflux.simulator.scenarios import (
    MANAGED_ALGORITHMS,
    MANAGED_BASELINE_FACTORIES,
    apply_chat_prefs,
    apply_external_guidance,
    normalize_managed_config,
    provision_managed_vpp,
    validate_managed_agent_params,
)

router = APIRouter(prefix="/vpps", tags=["vpps"])

# Tier A3 guidance ingestion: ~2/min sustained per account (comparable to the platform
# strategist's own 60-tick refresh cadence), small burst for catch-up after reconnect.
_guidance_limiter = RateLimiter(capacity=10, refill_per_sec=1 / 30)
# Owner chatroom posts: human cadence (burst of 5, then one line every ~8s).
_say_limiter = RateLimiter(capacity=5, refill_per_sec=1 / 8)

VPPParamValue = float | int | str | None
VPPParamsPayload = dict[str, VPPParamValue]


class VPPCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    params: dict[str, object] = Field(default_factory=dict)


class VPPOut(BaseModel):
    id: int
    name: str
    params: VPPParamsPayload
    is_active: bool
    is_external: bool
    created_at: datetime


class ManagedVPPOut(BaseModel):
    id: int
    vpp_id: int
    name: str
    params: VPPParamsPayload
    is_active: bool
    is_external: bool
    algorithm: str = "ppo"
    # Whether the LLM strategist is layered on the base algorithm (drives the "LLM + <ALGO>" label).
    llm_enabled: bool = False
    agent_kind: str
    strategy: str
    llm_live: bool
    llm_status: str
    # "live" | "degraded" | "offline" — computed from the agent's recent
    # reflection outcomes so the UI badge reflects reality, not startup state.
    llm_health_state: str
    persona: str | None = None  # the strategy brief, so the UI can pre-fill the tune form
    model: str | None = None  # the LLM model the agent's strategist runs on
    # Who steers the agent: "platform" (our LLM strategist), "external" (the owner's
    # own model via PUT /vpps/managed/{id}/guidance — Tier A3), or "none".
    guidance_source: str = "none"
    # Chatroom presence preferences (PUT /vpps/managed/{id}/chat).
    chat_style: str | None = None
    chat_color: str | None = None
    chat_avatar: str | None = None


class ManagedTradeOut(BaseModel):
    trade_id: int | str
    kind: str | None = None
    side: str
    price: str  # $/MWh
    raw_lmp: str | None = None  # $/MWh
    qty: str
    cash: str  # USD (converted from internal $/MWh x kWh units; see market.units)
    counterparty: str | None = None
    counterparty_vpp_id: int
    buy_vpp_id: int
    sell_vpp_id: int
    sim_ts: datetime
    wall_ts: datetime


class ReflectionEntryOut(BaseModel):
    ts: datetime
    ok: bool
    # Legacy ReflectiveAgent hint fields; null for HybridPolicyAgent strategist logs.
    price_adjust: float | None = None
    qty_scale: float | None = None
    # HybridPolicyAgent + LLMStrategist guidance fields.
    preferred_modes: list[str] | None = None
    avoid_modes: list[str] | None = None
    mode_pin: str | None = None
    risk_budget: float | None = None
    price_bias_bps: float | None = None
    soc_target: float | None = None
    execution_style: str | None = None
    rationale: str = ""
    # Durable takeaway the LLM distilled from the latest guidance/reflection cycle.
    # None for entries recorded before lessons existed.
    lesson: str | None = None
    meta_control: dict[str, float] | None = None
    error: str | None


class LLMHealthOut(BaseModel):
    ok_count: int
    fail_count: int
    last_ok_ts: datetime | None
    state: str  # "live" | "degraded" | "offline"


class ManagedVPPPerformanceOut(BaseModel):
    id: int
    name: str
    pnl: str  # USD (converted from internal $/MWh x kWh units; see market.units)
    cumulative_energy_bought_kwh: float
    cumulative_energy_sold_kwh: float
    imbalance_unserved_load_kwh: float = 0.0
    imbalance_spilled_generation_kwh: float = 0.0
    imbalance_settlement_cash: str = "0"
    soc_kwh: float
    soc_frac: float
    recent_trades: list[ManagedTradeOut]
    # LLM guidance/reflection audit trail, newest first. Empty for non-LLM agents.
    reflections: list[ReflectionEntryOut]
    llm_health: LLMHealthOut | None


@router.get("", response_model=list[VPPOut])
async def list_my_vpps(session: DbSession, user: CurrentUser) -> list[VPPOut]:
    # Only live VPPs — a soft-deleted (is_active=False) one is gone from the user's view.
    stmt = (
        select(VPP)
        .where(VPP.owner_id == user.id, VPP.is_managed.is_(False), VPP.is_active.is_(True))
        .order_by(VPP.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        VPPOut(
            id=v.id,
            name=v.name,
            params=v.params,
            is_active=v.is_active,
            is_external=v.is_external,
            created_at=v.created_at,
        )
        for v in rows
    ]


def _llm_health(vpp) -> tuple[str, LLMHealthOut | None]:
    """Derive the runtime LLM health from the agent's reflection counters.

    offline  — no live LLM client configured
    live     — at least one reflection succeeded and the most recent attempt did
    degraded — client configured but reflections are failing (or none succeeded yet)
    """
    agent = vpp.agent
    log_entries = list(getattr(agent, "reflection_log", []))
    ok_count = getattr(agent, "ok_count", 0)
    fail_count = getattr(agent, "fail_count", 0)
    last_ok_ts = getattr(agent, "last_ok_ts", None)

    if not vpp.llm_live:
        state = "offline"
    elif log_entries and log_entries[-1]["ok"]:
        state = "live"
    elif ok_count == 0 and fail_count == 0:
        state = "live"  # configured, no attempt yet — give it the benefit of the doubt
    else:
        state = "degraded"

    health = None
    if vpp.llm_live or ok_count or fail_count:
        health = LLMHealthOut(
            ok_count=ok_count, fail_count=fail_count, last_ok_ts=last_ok_ts, state=state
        )
    return state, health


def _guidance_source(vpp) -> str:
    strategist = getattr(vpp.agent, "strategist", None)
    if isinstance(strategist, ExternalStrategist):
        return "external"
    return "none" if strategist is None else "platform"


def _managed_vpp_out(vpp) -> ManagedVPPOut:
    state, health = _llm_health(vpp)
    status = vpp.llm_status
    if health is not None and (health.ok_count or health.fail_count):
        status = f"{status} — {health.ok_count} ok / {health.fail_count} failed"
    strategist = getattr(vpp.agent, "strategist", None)
    client = getattr(strategist, "client", None) if strategist is not None else None
    return ManagedVPPOut(
        id=vpp.managed_def_id if vpp.managed_def_id is not None else vpp.vpp_id,
        vpp_id=vpp.vpp_id,
        name=vpp.name,
        params=vpp.params.to_dict(),
        is_active=True,
        is_external=False,
        algorithm=getattr(vpp, "algorithm", None) or "ppo",
        llm_enabled=getattr(vpp, "llm_enabled", False),
        agent_kind=vpp.agent.__class__.__name__,
        strategy=vpp.strategy,
        llm_live=vpp.llm_live,
        llm_status=status,
        llm_health_state=state,
        persona=getattr(vpp.agent, "persona_prompt", None),
        model=getattr(client, "model", None),
        guidance_source=_guidance_source(vpp),
        chat_style=vpp.chat_style,
        chat_color=vpp.chat_color,
        chat_avatar=vpp.chat_avatar,
    )


@router.get("/managed", response_model=list[ManagedVPPOut])
async def list_my_managed_vpps(
    user: CurrentUser,
    sim: SimulatorDep,
) -> list[ManagedVPPOut]:
    return [_managed_vpp_out(vpp) for vpp in sim.my_managed_vpps(user.id)]


@router.get("/managed/{vpp_id}/performance", response_model=ManagedVPPPerformanceOut)
async def get_my_managed_vpp_performance(
    vpp_id: int,
    user: CurrentUser,
    sim: SimulatorDep,
) -> ManagedVPPPerformanceOut:
    vpp = next((v for v in sim.my_managed_vpps(user.id) if v.managed_def_id == vpp_id), None)
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "managed VPP not found")
    _, health = _llm_health(vpp)
    reflections = [
        ReflectionEntryOut(**entry)
        for entry in reversed(list(getattr(vpp.agent, "reflection_log", [])))
    ]
    imbalance_totals = sim.imbalance_totals(vpp.vpp_id)
    return ManagedVPPPerformanceOut(
        id=vpp.vpp_id,
        name=vpp.name,
        pnl=str(internal_cash_to_usd(vpp.state.pnl)),
        cumulative_energy_bought_kwh=vpp.state.cumulative_energy_bought_kwh,
        cumulative_energy_sold_kwh=vpp.state.cumulative_energy_sold_kwh,
        imbalance_unserved_load_kwh=imbalance_totals["unserved_load_kwh"],
        imbalance_spilled_generation_kwh=imbalance_totals["spilled_generation_kwh"],
        imbalance_settlement_cash=str(
            internal_cash_to_usd(Decimal(str(imbalance_totals["settlement_cash"])))
        ),
        soc_kwh=vpp.battery.soc_kwh,
        soc_frac=vpp.battery.soc_frac,
        recent_trades=[_managed_trade_out(t) for t in vpp.recent_trades[:25]],
        reflections=reflections,
        llm_health=health,
    )


def _managed_trade_out(record: dict) -> ManagedTradeOut:
    # Trade `price`/`raw_lmp` stay in $/MWh; only the settled `cash` total is
    # converted from internal units to USD for display (see market.units).
    out = {**record, "cash": str(internal_cash_to_usd(Decimal(str(record["cash"]))))}
    return ManagedTradeOut(**out)


class ModelsOut(BaseModel):
    models: list[str]
    default: str


@router.get("/models", response_model=ModelsOut)
async def list_models(user: CurrentUser) -> ModelsOut:
    """Curated LLM models a user can pick when deploying a managed agent."""
    from eflux.config import get_settings

    return ModelsOut(models=list(CURATED_MODELS), default=get_settings().llm_model)


def _validate_model(model: str | None) -> None:
    if model is not None and model not in CURATED_MODELS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            [
                {
                    "loc": ["body", "model"],
                    "msg": f"unknown model {model!r}; choose from {list(CURATED_MODELS)}",
                    "type": "value_error",
                }
            ],
        )


class AlgorithmParamOut(BaseModel):
    name: str
    type: str
    default: object = None
    min: float | None = None
    max: float | None = None
    help: str


class AlgorithmOut(BaseModel):
    id: str
    label: str
    description: str
    # Every base algorithm can be paired with the LLM strategist via the llm_enabled toggle.
    llm_capable: bool
    supports_online_learning: bool
    params: list[AlgorithmParamOut]


class AlgorithmsOut(BaseModel):
    algorithms: list[AlgorithmOut]
    default: str
    default_llm_enabled: bool = True


# The user-selectable *base* algorithms. Every base is `llm_capable`: pairing it with the LLM
# strategist (the `llm_enabled` toggle) yields the combinations shown in the Benchmark —
# "LLM + PPO" (the classic Hybrid stack), "LLM + AA", etc. Params are the base agent's own knobs;
# the strategist adds no extra user knobs (its fallback defaults to "hold").
_ALGORITHM_ROSTER = {
    "ppo": {
        "label": "PPO",
        "description": "Structured-policy tactical executor over the shared action space.",
        "supports_online_learning": True,
        "factory": None,
        "params": {
            "demand_beta": ("float", 0.0, 0.0, 5.0, "Scarcity sensitivity for buy bids."),
            "price_cap_mult": ("float", 1.5, 1.0, 10.0, "Maximum scarcity bid multiple."),
        },
    },
    "truthful": {
        "label": "Truthful",
        "description": "Cost/value baseline that quotes its valuation directly.",
        "supports_online_learning": False,
        "factory": MANAGED_BASELINE_FACTORIES["truthful"],
        "params": {
            "demand_beta": ("float", 0.0, 0.0, 5.0, "Scarcity sensitivity for buy bids."),
            "soc_high": ("float", 0.45, 0.0, 1.0, "Battery SOC threshold for sell quotes."),
            "soc_low": ("float", 0.25, 0.0, 1.0, "Battery SOC threshold for buy quotes."),
            "price_cap_mult": ("float", 1.5, 1.0, 10.0, "Maximum scarcity bid multiple."),
        },
    },
    "zip": {
        "label": "ZIP",
        "description": "Zero-Intelligence Plus adaptive-margin baseline.",
        "supports_online_learning": False,
        "factory": MANAGED_BASELINE_FACTORIES["zip"],
        "params": {
            "beta": ("float", 0.3, 0.0, 1.0, "Margin learning rate."),
            "momentum": ("float", 0.05, 0.0, 1.0, "Margin update momentum."),
            "rel_perturb": ("float", 0.05, 0.0, 1.0, "Relative target perturbation."),
            "abs_perturb": ("float", 0.5, 0.0, None, "Absolute target perturbation."),
            "init_margin": ("float", 0.05, 0.0, 1.0, "Initial profit margin."),
            "max_margin": ("float", 0.5, 0.0, 5.0, "Maximum profit margin."),
        },
    },
    "gd": {
        "label": "GD",
        "description": "Gjerstad-Dickhaut belief-based baseline.",
        "supports_online_learning": False,
        "factory": MANAGED_BASELINE_FACTORIES["gd"],
        "params": {},
    },
    "aa": {
        "label": "AA",
        "description": "Adaptive Aggressiveness baseline.",
        "supports_online_learning": False,
        "factory": MANAGED_BASELINE_FACTORIES["aa"],
        "params": {
            "pstar_alpha": ("float", 0.2, 0.0, 1.0, "EWMA weight for equilibrium price."),
            "learn_rate": ("float", 0.1, 0.0, 1.0, "Aggressiveness learning rate."),
            "passive_spread": ("float", 0.1, 0.0, 1.0, "Passive offset around p-star."),
        },
    },
}


def _algorithm_factory_fields(algorithm: str) -> set[str]:
    if algorithm == "hybrid":
        from eflux.agents.hybrid import HybridPolicyAgent

        factory = HybridPolicyAgent
    elif algorithm == "ppo":
        from eflux.agents.hybrid import StrategyAgent

        factory = StrategyAgent
    else:
        factory = MANAGED_BASELINE_FACTORIES[algorithm]
    return {f for f in factory.__dataclass_fields__ if not f.startswith("_")}


def _algorithm_out(algorithm: str) -> AlgorithmOut:
    entry = _ALGORITHM_ROSTER[algorithm]
    fields = _algorithm_factory_fields(algorithm)
    params: list[AlgorithmParamOut] = []
    for name, (typ, default, min_v, max_v, help_text) in entry["params"].items():
        if name not in fields:
            raise RuntimeError(f"{algorithm}: algorithm param {name!r} is not a dataclass field")
        params.append(
            AlgorithmParamOut(
                name=name,
                type=typ,
                default=default,
                min=min_v,
                max=max_v,
                help=help_text,
            )
        )
    return AlgorithmOut(
        id=algorithm,
        label=entry["label"],
        description=entry["description"],
        llm_capable=True,
        supports_online_learning=entry["supports_online_learning"],
        params=params,
    )


@router.get("/algorithms", response_model=AlgorithmsOut)
async def list_algorithms(user: CurrentUser) -> AlgorithmsOut:
    """Managed-agent base algorithms available at creation time. Each can be paired with the LLM
    strategist via the llm_enabled toggle — the same public roster the Benchmark reports."""

    return AlgorithmsOut(
        algorithms=[_algorithm_out(algorithm) for algorithm in MANAGED_ALGORITHMS],
        default="ppo",
        default_llm_enabled=True,
    )


MAX_MANAGED_VPPS_PER_USER = 5


class ManagedVPPCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    params: dict[str, object] = Field(default_factory=dict)
    # The base tactical algorithm; llm_enabled layers the LLM strategist on top of it.
    algorithm: Literal["ppo", "truthful", "zip", "gd", "aa"] = "ppo"
    llm_enabled: bool = True
    online_learning: bool = True
    # Optional LLM strategy brief (the Tier-0 persona), the LLM model, and tactical agent_params
    # (e.g. demand_beta, price_cap_mult) — the persona/model apply only when the LLM is enabled.
    persona: str | None = Field(default=None, max_length=600)
    agent_params: dict[str, object] = Field(default_factory=dict)
    seed: int | None = None
    model: str | None = None

    @model_validator(mode="after")
    def _check_llm_only_fields(self) -> ManagedVPPCreate:
        if not self.llm_enabled and (self.persona is not None or self.model is not None):
            raise ValueError("persona and model are only supported when llm_enabled is true")
        return self


class ManagedVPPUpdate(BaseModel):
    # Only provided fields change: params merges into the stored DER config, agent_params
    # replaces the stored set, persona "" clears it / null leaves it unchanged, model null
    # leaves it unchanged.
    params: dict[str, object] | None = None
    persona: str | None = Field(default=None, max_length=600)
    agent_params: dict[str, object] | None = None
    model: str | None = None


@router.post("/managed", response_model=ManagedVPPOut, status_code=status.HTTP_201_CREATED)
async def create_managed_vpp(
    payload: ManagedVPPCreate,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> ManagedVPPOut:
    """Provision a cloud-hosted, LLM-steered managed agent — Tier 0 of
    docs/EXTERNAL_PARTICIPATION.md. The platform runs the HybridPolicyAgent (LLM strategist +
    PPO executor + Truthful oracle + RiskGate) autonomously on the user's behalf; the user
    supplies only a DER endowment and an optional persona/preferences. Params and agent_params
    are validated against the same schema as the built-in roster (422 on bad input)."""
    # Quota + name checks against the LIVE agents (what the user actually sees), so a name
    # whose agent isn't currently running is reusable even if a stale definition lingers.
    live = sim.my_managed_vpps(user.id)
    if any(v.name == payload.name for v in live):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a running managed agent named {payload.name!r} already exists — delete it to reuse the name",
        )
    if len(live) >= MAX_MANAGED_VPPS_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"managed-agent limit reached ({MAX_MANAGED_VPPS_PER_USER} per account)",
        )
    # Validate the DER params up front (same 422 shape as POST /vpps).
    try:
        parsed = validate_vpp_params(payload.params)
    except ValidationError as e:
        detail = [
            {"loc": ["body", "params", *err["loc"]], "msg": err["msg"], "type": err["type"]}
            for err in e.errors(include_url=False)
        ]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail) from e
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            [{"loc": ["body", "params"], "msg": str(e), "type": "value_error"}],
        ) from e
    if payload.llm_enabled:
        _validate_model(payload.model)
    try:
        validate_managed_agent_params(
            payload.name, payload.agent_params, payload.algorithm, llm_enabled=payload.llm_enabled
        )
    except (ValueError, ValidationError) as e:
        msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            [{"loc": ["body", "agent_params"], "msg": msg, "type": "value_error"}],
        ) from e
    # Drop any orphaned definition with this name (its agent isn't live — e.g. it failed to
    # rehydrate) so the unique (owner, name) row is free to recreate.
    orphan = (
        await session.execute(
            select(VPP).where(
                VPP.owner_id == user.id, VPP.name == payload.name, VPP.is_managed.is_(True)
            )
        )
    ).scalar_one_or_none()
    if orphan is not None:
        await session.delete(orphan)
        await session.flush()
    # Persist the definition first — its row id is the stable handle the agent is bound to.
    row = VPP(
        owner_id=user.id,
        name=payload.name,
        params=parsed,
        is_external=True,
        is_managed=True,
        managed_config={
            "persona": payload.persona,
            "agent_params": dict(payload.agent_params),
            "seed": payload.seed,
            "model": payload.model,
            "algorithm": payload.algorithm,
            "llm_enabled": payload.llm_enabled,
            "online_learning": payload.online_learning,
        },
    )
    session.add(row)
    try:
        await session.flush()
    except Exception as e:
        raise HTTPException(status.HTTP_409_CONFLICT, f"name conflict: {e}") from e
    # Provision the live agent bound to the stable id. agent_params validation happens here; on
    # failure the exception propagates and get_db rolls the row back — no orphaned definition.
    try:
        async with sim._lock:
            vpp = provision_managed_vpp(
                sim,
                owner_id=user.id,
                name=payload.name,
                params=parsed,
                persona_prompt=payload.persona,
                agent_params=payload.agent_params,
                seed=payload.seed,
                model=payload.model,
                managed_def_id=row.id,
                algorithm=payload.algorithm,
                llm_enabled=payload.llm_enabled,
                online_learning=payload.online_learning,
            )
    except (ValueError, ValidationError) as e:
        msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            [{"loc": ["body", "agent_params"], "msg": msg, "type": "value_error"}],
        ) from e
    return _managed_vpp_out(vpp)


@router.patch("/managed/{managed_id}", response_model=ManagedVPPOut)
async def update_managed_vpp(
    managed_id: int,
    payload: ManagedVPPUpdate,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> ManagedVPPOut:
    """Adjust a managed agent's trading preferences (persona, agent_params, DER params) and
    re-provision it. Applying changes restarts the agent's trading session — open orders reset
    and SOC returns to default — but the PnL scoreboard (realized PnL, energy traded, trade
    count) carries over. Ownership enforced; validation mirrors creation (422 on bad input)."""
    row = (
        await session.execute(
            select(VPP).where(
                VPP.id == managed_id, VPP.owner_id == user.id, VPP.is_managed.is_(True)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "managed agent not found")

    cfg = dict(row.managed_config or {})
    algorithm, llm_enabled = normalize_managed_config(cfg)
    persona_change = "persona" in payload.model_fields_set and payload.persona is not None
    model_change = "model" in payload.model_fields_set and payload.model is not None
    if not llm_enabled and (persona_change or model_change):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            [
                {
                    "loc": ["body"],
                    "msg": "persona and model changes are only supported when the LLM strategist is enabled",
                    "type": "value_error",
                }
            ],
        )
    new_params = {**row.params, **payload.params} if payload.params else dict(row.params)
    new_persona = cfg.get("persona") if payload.persona is None else (payload.persona or None)
    new_agent_params = (
        dict(cfg.get("agent_params") or {})
        if payload.agent_params is None
        else dict(payload.agent_params)
    )
    new_seed = cfg.get("seed")
    new_model = cfg.get("model") if payload.model is None else payload.model
    if llm_enabled:
        _validate_model(new_model)

    # Validate everything BEFORE touching the live agent, so a bad patch can't strand it.
    try:
        parsed = validate_vpp_params(new_params)
        validate_managed_agent_params(row.name, new_agent_params, algorithm, llm_enabled=llm_enabled)
    except ValidationError as e:
        detail = [
            {"loc": ["body", *err["loc"]], "msg": err["msg"], "type": err["type"]}
            for err in e.errors(include_url=False)
        ]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail) from e
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            [{"loc": ["body"], "msg": str(e), "type": "value_error"}],
        ) from e

    # Update the stored definition (re-assign so SQLAlchemy tracks the JSON change).
    # MERGE into the existing config: it also carries guidance_mode/external_guidance
    # (Tier A3) and chat prefs, which a wholesale rewrite would silently drop.
    row.params = parsed
    row.managed_config = {
        **cfg,
        "persona": new_persona,
        "agent_params": new_agent_params,
        "seed": new_seed,
        "model": new_model,
        "algorithm": algorithm,
        "llm_enabled": llm_enabled,
        "online_learning": cfg.get("online_learning", True),
    }

    # Re-provision the live agent, carrying over the PnL scoreboard.
    async with sim._lock:
        old = next((v for v in sim.vpps.values() if v.managed_def_id == managed_id), None)
        carry = (
            (
                old.state.pnl,
                old.state.cumulative_energy_bought_kwh,
                old.state.cumulative_energy_sold_kwh,
                old.trade_count,
                list(old.recent_trades),
            )
            if old is not None
            else None
        )
        sim.remove_managed_vpp(managed_id)
        vpp = provision_managed_vpp(
            sim,
            owner_id=user.id,
            name=row.name,
            params=parsed,
            persona_prompt=new_persona,
            agent_params=new_agent_params,
            seed=new_seed,
            model=new_model,
            managed_def_id=managed_id,
            algorithm=algorithm,
            llm_enabled=llm_enabled,
            online_learning=cfg.get("online_learning", True),
        )
        if carry is not None:
            (
                vpp.state.pnl,
                vpp.state.cumulative_energy_bought_kwh,
                vpp.state.cumulative_energy_sold_kwh,
                vpp.trade_count,
                vpp.recent_trades,
            ) = carry
        # The fresh agent instance must inherit the owner's standing state: external
        # steering (Tier A3) and chatroom presence both survive a preferences patch.
        guidance = cfg.get("external_guidance")
        if llm_enabled and cfg.get("guidance_mode") == "external" and isinstance(guidance, dict):
            apply_external_guidance(vpp, guidance, market_mode=sim.market_mode)
        apply_chat_prefs(vpp, cfg.get("chat"))
    return _managed_vpp_out(vpp)


class ChatPrefsIn(BaseModel):
    """Chatroom presence for a managed agent. All optional; null clears a field."""

    style: str | None = Field(default=None, max_length=200)  # chat-only voice hint
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    avatar: str | None = Field(default=None, max_length=4)  # one emoji / short glyph


class SayIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class ChatPostOut(BaseModel):
    name: str
    wall_ts: datetime
    text: str
    color: str | None
    avatar: str | None
    source: str


def _live_managed_vpp(sim, user_id: int, managed_id: int):
    vpp = next((v for v in sim.my_managed_vpps(user_id) if v.managed_def_id == managed_id), None)
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "managed VPP not found")
    return vpp


@router.put("/managed/{managed_id}/chat", response_model=ManagedVPPOut)
async def set_chat_prefs(
    managed_id: int,
    payload: ChatPrefsIn,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> ManagedVPPOut:
    """Set your agent's chatroom presence: a chat-only voice for its LLM banter, and a
    display color / emoji avatar. Display-and-prompt only; trading is untouched, and no
    agent restart happens."""
    vpp = _live_managed_vpp(sim, user.id, managed_id)
    row = await _owned_managed_row(session, user.id, managed_id)
    chat = {"style": payload.style, "color": payload.color, "avatar": payload.avatar}
    row.managed_config = {**dict(row.managed_config or {}), "chat": chat}
    apply_chat_prefs(vpp, chat)
    return _managed_vpp_out(vpp)


@router.post("/managed/{managed_id}/say", response_model=ChatPostOut)
async def say_in_chatroom(
    managed_id: int,
    payload: SayIn,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> ChatPostOut:
    """Speak in the public agent chatroom as your managed agent. The line is posted under
    the agent's name (tagged as owner-written) into the same room the LLM agents read,
    so they can react to you. Rate limited to a human cadence."""
    allowed, remaining = _say_limiter.check(user.id, 1)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"chat rate limit exceeded — {remaining} messages left, refills at ~1 per 8s",
        )
    vpp = _live_managed_vpp(sim, user.id, managed_id)
    await _owned_managed_row(session, user.id, managed_id)  # ownership re-check vs DB
    text = clean_chat_line(payload.text)
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "message is empty after cleanup")
    entry = sim.post_chat(vpp, text, source="owner")
    return ChatPostOut(**entry)


class GuidanceIn(BaseModel):
    """Tier A3 payload — exactly the StrategyGuidance (+ optional MetaControl) shape.
    Soft by design: unknown mode names are dropped and numbers are clamped server-side;
    the response echoes what was actually applied."""

    preferred_modes: list[str] = Field(default_factory=list, max_length=8)
    avoid_modes: list[str] = Field(default_factory=list, max_length=8)
    mode_pin: str | None = None
    risk_budget: float = 1.0
    price_bias_bps: float = 0.0
    soc_target: float = 0.5
    execution_style: str = Field(default="", max_length=200)
    lesson: str = Field(default="", max_length=200)
    meta_control: dict[str, float] | None = None


class GuidanceOut(BaseModel):
    managed_id: int
    guidance_source: str
    # The clamped, market-sanitized guidance as recorded — same shape as the
    # reflections feed, so clients parse one schema everywhere.
    applied: ReflectionEntryOut
    applied_at: datetime


async def _owned_managed_row(
    session, user_id: int, managed_id: int
) -> VPP:
    row = (
        await session.execute(
            select(VPP).where(
                VPP.id == managed_id, VPP.owner_id == user_id, VPP.is_managed.is_(True)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "managed agent not found")
    return row


@router.put("/managed/{managed_id}/guidance", response_model=GuidanceOut)
async def put_guidance(
    managed_id: int,
    payload: GuidanceIn,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> GuidanceOut:
    """Steer your managed agent with your own model — Tier A3 of
    docs/EXTERNAL_PARTICIPATION.md. The posted StrategyGuidance replaces the platform
    LLM strategist's steering (which stops being called — running your own model costs
    zero platform LLM budget) while the platform's PPO executor, order compiler, and
    RiskGate keep doing the execution. Guidance stays soft: clamped on arrival, biases
    but never commands (DELETE the guidance to hand control back to the platform LLM).
    Persisted with the agent definition, so it survives a backend restart."""
    allowed, remaining = _guidance_limiter.check(user.id, 1)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"guidance rate limit exceeded — {remaining} tokens left, refills at ~2/min",
        )
    vpp = next((v for v in sim.my_managed_vpps(user.id) if v.managed_def_id == managed_id), None)
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "managed VPP not found")
    if not getattr(vpp, "llm_enabled", False):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "external guidance is only supported for LLM-steered managed agents",
        )
    row = await _owned_managed_row(session, user.id, managed_id)

    async with sim._lock:
        entry = apply_external_guidance(vpp, payload.model_dump(), market_mode=sim.market_mode)
    # Persist the raw payload; rehydration re-clamps through the same path.
    row.managed_config = {
        **dict(row.managed_config or {}),
        "guidance_mode": "external",
        "external_guidance": payload.model_dump(),
    }
    return GuidanceOut(
        managed_id=managed_id,
        guidance_source="external",
        applied=ReflectionEntryOut(**entry),
        applied_at=entry["ts"],
    )


@router.delete("/managed/{managed_id}/guidance", status_code=status.HTTP_204_NO_CONTENT)
async def release_guidance(
    managed_id: int,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> None:
    """Hand steering back to the platform LLM strategist (idempotent — releasing an
    agent that isn't externally steered is a no-op)."""
    vpp = next((v for v in sim.my_managed_vpps(user.id) if v.managed_def_id == managed_id), None)
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "managed VPP not found")
    if not getattr(vpp, "llm_enabled", False):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "external guidance is only supported for LLM-steered managed agents",
        )
    row = await _owned_managed_row(session, user.id, managed_id)

    async with sim._lock:
        strategist = getattr(vpp.agent, "strategist", None)
        if isinstance(strategist, ExternalStrategist):
            vpp.agent.strategist = strategist.prior
    cfg = dict(row.managed_config or {})
    cfg.pop("external_guidance", None)
    cfg["guidance_mode"] = "platform"
    row.managed_config = cfg
    return None


@router.post("", response_model=VPPOut, status_code=status.HTTP_201_CREATED)
async def create_vpp(payload: VPPCreate, session: DbSession, user: CurrentUser) -> VPPOut:
    # Same validation path as the built-in roster (simulator/agent_spec.py) —
    # internal and external participants share one params schema.
    # ValueError also covers the unknown-keys rejection; the detail mirrors
    # FastAPI's native 422 shape (a list of {loc, msg, type}) so clients can
    # parse every validation failure on this endpoint the same way.
    try:
        parsed = validate_vpp_params(payload.params)
    except ValidationError as e:
        detail = [
            {
                "loc": ["body", "params", *err["loc"]],
                "msg": err["msg"],
                "type": err["type"],
            }
            for err in e.errors(include_url=False)
        ]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail) from e
    except ValueError as e:
        detail = [{"loc": ["body", "params"], "msg": str(e), "type": "value_error"}]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail) from e
    vpp = VPP(
        owner_id=user.id,
        name=payload.name,
        params=parsed,
        is_external=True,  # user-created → external SDK or UI driver
    )
    session.add(vpp)
    try:
        await session.flush()
    except Exception as e:
        raise HTTPException(status.HTTP_409_CONFLICT, f"name conflict: {e}") from e
    return VPPOut(
        id=vpp.id,
        name=vpp.name,
        params=vpp.params,
        is_active=vpp.is_active,
        is_external=vpp.is_external,
        created_at=vpp.created_at,
    )


@router.delete("/{vpp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_vpp(vpp_id: int, session: DbSession, user: CurrentUser) -> None:
    # Passive (order-driven) VPPs only; managed agents go through DELETE /vpps/managed/{id}.
    stmt = select(VPP).where(
        VPP.id == vpp_id, VPP.owner_id == user.id, VPP.is_managed.is_(False)
    )
    vpp = (await session.execute(stmt)).scalar_one_or_none()
    if vpp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "VPP not found")
    vpp.is_active = False
    return None


@router.delete("/managed/{managed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_managed_vpp(
    managed_id: int,
    session: DbSession,
    user: CurrentUser,
    sim: SimulatorDep,
) -> None:
    """Remove a managed agent: drop it from the simulator (cancelling its resting orders) and
    delete its persisted definition. Ownership enforced (404 on anything that isn't yours)."""
    stmt = select(VPP).where(
        VPP.id == managed_id, VPP.owner_id == user.id, VPP.is_managed.is_(True)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "managed agent not found")
    async with sim._lock:
        sim.remove_managed_vpp(managed_id)
    await session.delete(row)
    return None
