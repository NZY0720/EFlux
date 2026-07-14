"""Built-in scenario loading — roster comes from a YAML file (scenarios/default.yaml).

Every entry is validated as an AgentSpec (see simulator/agent_spec.py — the same
schema external participants integrate against). Strategy kinds: truthful | gas |
strategy | hybrid | zip | gd | aa. Hybrid LLM-managed entries share one SharedLLM
connection and get evenly staggered strategist refresh offsets so the single slow
endpoint is never hit concurrently.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from eflux.agents.aa_agent import AAAgent
from eflux.agents.base import BaseAgent
from eflux.agents.character import derive_character
from eflux.agents.gas import GasGeneratorAgent
from eflux.agents.gd_agent import GDAgent
from eflux.agents.hybrid import HybridPolicyAgent, StrategyAgent
from eflux.agents.llm.pool import SharedLLM
from eflux.agents.llm.strategist import LLMStrategist
from eflux.agents.strategy.policy import BaselinePolicy, ScriptedStrategyPolicy, StrategyPolicy
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.zip_agent import ZIPAgent
from eflux.compat.v0 import normalize_managed_config_v0
from eflux.config import PROJECT_ROOT, get_settings
from eflux.data.caiso_reference import caiso_reference_price
from eflux.simulator.agent_spec import AgentSpec, ExecutorSpec, validate_vpp_params
from eflux.simulator.runner import Simulator, SimulatorVPP
from eflux.simulator.scenario_spec import load_scenario_spec
from eflux.vpp.base import VPPParams

log = logging.getLogger(__name__)

AGENT_FACTORIES: dict[str, type[BaseAgent]] = {
    "truthful": TruthfulAgent,
    "gas": GasGeneratorAgent,
    "strategy": StrategyAgent,
    "hybrid": HybridPolicyAgent,
    # Classical quantitative baselines (continuous double auction). Each reuses the
    # truthful valuation oracle for its private value and adds an adaptive bidding rule.
    "zip": ZIPAgent,
    "gd": GDAgent,
    "aa": AAAgent,
}

# The user-selectable *base* algorithms (Deploy-a-cloud-hosted-agent picker / Benchmark).
# Any base can be paired with the LLM strategist via the llm_enabled toggle: "ppo" + LLM is
# the classic Hybrid stack; a baseline + LLM wraps that baseline as a BaselinePolicy executor.
MANAGED_ALGORITHMS = ("ppo", "truthful", "zip", "gd", "aa")
# Immutable Releases may select the platform's deterministic structured policy,
# but it is intentionally not added to the user-facing managed-agent picker.
RUNTIME_MANAGED_ALGORITHMS = (*MANAGED_ALGORITHMS, "scripted")
MANAGED_BASELINE_FACTORIES: dict[str, type[BaseAgent]] = {
    "truthful": TruthfulAgent,
    "zip": ZIPAgent,
    "gd": GDAgent,
    "aa": AAAgent,
}


@dataclass(frozen=True, slots=True)
class ManagedBrain:
    """Side-effect-free managed-agent construction result."""

    agent: BaseAgent
    params: VPPParams
    seed: int
    strategy: str
    llm_live: bool
    llm_status: str
    algorithm: str
    llm_enabled: bool

# Cost-based agents whose price_ref is re-based to the CAISO reference + jittered for cost
# diversification (LLM/gas/strategy-with-pinned-ref are handled elsewhere).
_COST_DIVERSIFIED_KINDS = frozenset({"truthful", "strategy", "zip", "gd", "aa"})

# Dataclass fields that hold injected policy/strategist objects, never YAML kwargs.
_INJECTED_FIELDS = frozenset(
    {
        "policy",
        "executor",
        "strategist",
        "fallback",
        "refresh_every_n_ticks",
        "refresh_offset_ticks",
        "persona_prompt",
    }
)


def _validate_agent_params(name: str, agent_params: dict, factory: type) -> None:
    """Reject agent_params keys the target agent's constructor doesn't accept, at
    load time with a named error. Otherwise `factory(**agent_params)` raises a bare
    TypeError mid-load (e.g. a strategy entry passing truthful-only soc_high)."""
    fields = {
        f for f in factory.__dataclass_fields__ if not f.startswith("_") and f not in _INJECTED_FIELDS
    }
    unknown = sorted(set(agent_params) - fields)
    if unknown:
        raise ValueError(
            f"{name!r}: agent_params {unknown} not accepted by {factory.__name__} "
            f"(valid: {sorted(fields)})"
        )


def _resolve_checkpoint(ckpt: str | None) -> Path | None:
    """Resolve a (possibly relative) checkpoint path against the project root; None if it
    is unset or does not exist."""
    if not ckpt:
        return None
    path = Path(ckpt)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path if path.exists() else None


def _build_executor(name: str, executor, *, seed: int = 0, auto_update: bool = True):
    """Build the tactical policy for a strategy/hybrid entry from its ExecutorSpec.

    None / scripted → None (the agent uses its own ScriptedStrategyPolicy default).
    ppo_online → a custom live-learning OnlinePPOPolicy (warm-started from a BC/online
    checkpoint when present, else a fresh net). Any failure (missing 'ai' extras or
    checkpoint, load error) falls back to scripted with a warning — a bad executor must not
    take down startup."""
    if executor is None or executor.kind == "scripted":
        return None

    if executor.kind == "ppo_online":
        try:
            from eflux.agents.ppo.online_ppo import build_online_policy
        except ImportError as e:
            log.warning("%s: ppo_online executor needs the 'ai' extras (%s) — using scripted", name, e)
            return None
        path = _resolve_checkpoint(executor.checkpoint)
        if executor.checkpoint and path is None:
            log.warning("%s: ppo_online warm-start %r missing — starting from a fresh net",
                        name, executor.checkpoint)
        learning = executor.online_learning and get_settings().online_learning_enabled
        try:
            return build_online_policy(
                str(path) if path else None, learning=learning, auto_update=auto_update, seed=seed
            )
        except Exception:
            log.exception("%s: ppo_online executor failed to build — using scripted", name)
            return None

    return None


def _managed_executor_spec(
    *, online_learning: bool, checkpoint: str | None = None
) -> ExecutorSpec:
    checkpoint = checkpoint or get_settings().managed_ppo_checkpoint or None
    return ExecutorSpec(
        kind="ppo_online",
        checkpoint=checkpoint,
        online_learning=online_learning,
    )


def _managed_factory(algorithm: str, *, llm_enabled: bool = False) -> type[BaseAgent]:
    """The dataclass the managed agent_params are validated against. ``ppo`` maps to the
    LLM-steered HybridPolicyAgent when the strategist is enabled (it accepts the extra
    strategist knobs, e.g. fallback_policy) and the bare StrategyAgent otherwise; a classical
    baseline validates against its own factory whether or not an LLM coaches it — the strategist
    wraps the baseline as a BaselinePolicy, it does not change the baseline's params."""
    if algorithm in ("ppo", "scripted"):
        return HybridPolicyAgent if llm_enabled else StrategyAgent
    try:
        return MANAGED_BASELINE_FACTORIES[algorithm]
    except KeyError as e:
        raise ValueError(
            f"unknown managed algorithm {algorithm!r}; choose from {list(MANAGED_ALGORITHMS)}"
        ) from e


def _managed_agent_params(
    name: str, agent_params: dict | None, algorithm: str, *, llm_enabled: bool = False
) -> dict:
    factory = _managed_factory(algorithm, llm_enabled=llm_enabled)
    params = dict(agent_params or {})
    _validate_agent_params(name, params, factory)
    if "price_ref" not in params and "price_ref" in factory.__dataclass_fields__:
        params["price_ref"] = _ppo_price_ref()
    return params


def _real_pv_available() -> bool:
    """True iff pvlib is installed AND user hasn't opted out via EFLUX_PV_PHYSICAL=false."""
    if os.environ.get("EFLUX_PV_PHYSICAL", "auto").lower() in ("false", "0", "off"):
        return False
    try:
        import pvlib  # noqa: F401
        return True
    except ImportError:
        return False


def _ppo_price_ref() -> Decimal:
    """The fixed CAISO-mean reference PPO/hybrid agents pin their valuation to — unjittered, so
    it matches the normalization scale the checkpoints were trained under (obs parity)."""
    return Decimal(str(caiso_reference_price()))


def _set_ppo_scale() -> None:
    """Fix this process's PPO normalization scale to the trailing-month CAISO mean (the same
    quantity training stamps into the checkpoint). No-op without the 'ai' extras / numpy."""
    try:
        from eflux.agents.ppo.primitive_encoding import set_price_ref_scale
    except ImportError:
        return
    set_price_ref_scale(caiso_reference_price())


def load_default_scenario(sim: Simulator) -> None:
    """Load and validate the YAML roster; wire LLM-managed entries to the shared LLM."""
    settings = get_settings()
    _set_ppo_scale()
    path = Path(settings.scenario_file)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    scenario = load_scenario_spec(path)
    if scenario.market_mode != "any" and scenario.market_mode != settings.market_mode:
        raise ValueError(
            f"Scenario file {path} targets market_mode={scenario.market_mode!r}, "
            f"not {settings.market_mode!r}"
        )
    specs = list(scenario.participants)

    use_real_weather = _real_pv_available()
    managed_specs = [s for s in specs if s.agent == "hybrid"]
    # Build the shared LLM connection unconditionally and retain it on the simulator, so the
    # API can provision managed agents at runtime even when the roster declares none.
    shared = SharedLLM.from_settings(settings)
    sim.shared_llm = shared

    counts: dict[str, int] = {}
    managed_index = 0
    for i, spec in enumerate(specs):
        if spec.agent == "hybrid":
            _add_hybrid_vpp(
                sim,
                spec,
                shared=shared,
                use_real_weather=use_real_weather,
                default_seed=42 + i,
                offset_index=managed_index,
                n_managed=len(managed_specs),
            )
            managed_index += 1
            if spec.mirror:
                _add_mirror_vpp(sim, spec, use_real_weather=use_real_weather, default_seed=42 + i)
                counts["mirror"] = counts.get("mirror", 0) + 1
        else:
            factory = AGENT_FACTORIES[spec.agent]
            _validate_agent_params(spec.name, spec.agent_params, factory)
            seed = spec.seed if spec.seed is not None else 42 + i
            # A `strategy` entry may carry a learned executor (the standalone PPO VPP). Such
            # agents pin price_ref to the unjittered CAISO reference (matching the checkpoint's
            # training scale) rather than taking the cost-diversification jitter.
            if spec.agent == "strategy" and spec.executor is not None:
                agent_params = dict(spec.agent_params)
                agent_params.setdefault("price_ref", _ppo_price_ref())
                agent_params.setdefault("use_forecast", True)
                agent_params["policy"] = _build_executor(
                    spec.name, spec.executor, seed=seed, auto_update=True
                )
            else:
                agent_params = _diversify_cost(spec, seed, settings.price_ref_jitter_frac, factory)
                if "use_forecast" in factory.__dataclass_fields__:
                    agent_params.setdefault("use_forecast", True)
            params = _build_params(spec, use_real_weather)
            if settings.agent_character_enabled and "character" in factory.__dataclass_fields__:
                agent_params.setdefault("character", derive_character(params))
            sim.add_builtin_vpp(
                name=spec.name,
                params=params,
                agent=factory(**agent_params),
                seed=seed,
            )
        counts[spec.agent] = counts.get(spec.agent, 0) + 1

    log.info(
        "Scenario %s: %d VPPs loaded (%s, real_weather=%s)",
        path.name,
        len(specs),
        ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        use_real_weather,
    )

    log.info(
        "Default scenario ready: %d ordinary agents + %d LLM agents",
        len(specs) - len(managed_specs),
        len(sim.my_managed_vpps()),
    )


def _diversify_cost(
    spec: AgentSpec, seed: int, jitter_frac: float, factory: type[BaseAgent]
) -> dict:
    """Spread cost levels across non-LLM agents by jittering price_ref.

    Every truthful/ZI agent otherwise shares price_ref=50, so their battery-band
    asks (price_ref/√eta ≈ 52.7) and deficit bids land on identical price levels
    and the market clears at ~2 discrete prints. A small deterministic per-agent
    offset (seeded by name+seed, so it's stable across restarts) fans those
    levels out into a band. LLM-managed agents never reach here — they are
    handled on the hybrid branch — so the LLM fleet is excluded by construction.
    Gas has no price_ref (its cost is per-VPP already) and is left untouched, as
    is any agent whose roster entry pins price_ref explicitly.
    """
    agent_params = dict(spec.agent_params)
    if (
        jitter_frac <= 0
        or spec.agent not in _COST_DIVERSIFIED_KINDS
        or "price_ref" in agent_params
    ):
        return agent_params
    # Base the cost reference on the trailing-month CAISO mean (calibrates the whole price
    # band to real grid levels) rather than the hardcoded dataclass default. Falls back to
    # the dataclass default / 50 when CAISO is unreachable.
    field = factory.__dataclass_fields__.get("price_ref")
    default_base = float(field.default) if field is not None else 50.0  # type: ignore[union-attr]
    base = caiso_reference_price(default=default_base)
    rng = random.Random(f"price_ref::{spec.name}::{seed}")
    price_ref = round(base * (1.0 + rng.uniform(-jitter_frac, jitter_frac)), 4)
    agent_params["price_ref"] = Decimal(str(price_ref))
    return agent_params


def _build_params(spec: AgentSpec, use_real_weather: bool) -> VPPParams:
    settings = get_settings()
    params_dict = dict(spec.params)
    if not use_real_weather:
        # Strip site coords → no Open-Meteo fetch, stub PV + wind models.
        params_dict.pop("pv_lat", None)
        params_dict.pop("pv_lon", None)
    elif params_dict.get("wind_kw_rated", 0.0) > 0:
        # The configured market region is CAISO SP15, so any real-weather DER
        # should use the same regional weather signal rather than legacy HK coords.
        params_dict["pv_lat"] = settings.site_wind_lat
        params_dict["pv_lon"] = settings.site_wind_lon
    elif params_dict.get("pv_kw_peak", 0.0) > 0:
        params_dict["pv_lat"] = settings.site_default_lat
        params_dict["pv_lon"] = settings.site_default_lon
    # Build from the *validated/coerced* dict, not the raw YAML values —
    # from_dict alone would happily store battery_kwh: "12" (a string) and
    # blow up mid-run on the first arithmetic touch.
    return VPPParams.from_dict(validate_vpp_params(params_dict))


def _build_hybrid_agent(
    spec: AgentSpec,
    *,
    shared: SharedLLM,
    use_real_weather: bool,
    default_seed: int,
    offset_index: int,
    n_managed: int,
    model: str | None = None,
    executor_override: StrategyPolicy | None = None,
    strategy_label: str | None = None,
) -> tuple[HybridPolicyAgent, VPPParams, int, str]:
    """Construct one HybridPolicyAgent without registering a participant.

    Offsets are spread evenly over the reflection interval (index-based —
    deterministic, no collisions) so calls to the single endpoint stagger
    instead of landing on the same tick.

    `executor_override` swaps in a pre-built tactical policy (e.g. a BaselinePolicy wrapping
    AA/ZIP/GD/Truthful) in place of the default PPO executor, so the same LLM strategist coaches
    a classical baseline.
    """
    settings = get_settings()
    interval = settings.llm_guidance_interval_ticks
    offset = round(offset_index * interval / max(1, n_managed))
    _validate_agent_params(spec.name, spec.agent_params, HybridPolicyAgent)
    model_client = shared.client_for(model)
    strategist = (
        LLMStrategist(
            client=model_client,
            persona_prompt=spec.persona.prompt if spec.persona else None,
            hard_timeout_sec=max(settings.llm_timeout_sec, 1.0) + 60.0,
            llm_gate=shared.gate,
        )
        if model_client is not None
        else None
    )
    seed = spec.seed if spec.seed is not None else default_seed
    agent_params = dict(spec.agent_params)
    agent_params.setdefault("price_ref", _ppo_price_ref())  # match the PPO normalization scale
    agent_params.setdefault("use_forecast", True)
    hybrid_params = _build_params(spec, use_real_weather)
    if settings.agent_character_enabled:
        agent_params.setdefault("character", derive_character(hybrid_params))
    executor = (
        executor_override
        if executor_override is not None
        else _build_executor(
            spec.name, spec.executor, seed=seed, auto_update=not settings.online_update_async
        )
    )
    agent = HybridPolicyAgent(
        **agent_params,
        executor=executor,
        strategist=strategist,
        refresh_every_n_ticks=interval,
        refresh_offset_ticks=offset,
        persona_prompt=spec.persona.prompt if spec.persona else None,
    )
    strategy = strategy_label or (
        f"HybridPolicyAgent ({model or shared.default_model or shared.strategy_suffix})"
    )
    return agent, hybrid_params, seed, strategy


def _add_hybrid_vpp(
    sim: Simulator,
    spec: AgentSpec,
    *,
    shared: SharedLLM,
    use_real_weather: bool,
    default_seed: int,
    offset_index: int,
    n_managed: int,
    owner_id: int | None = None,
    model: str | None = None,
    executor_override: StrategyPolicy | None = None,
    algorithm: str = "ppo",
    strategy_label: str | None = None,
) -> SimulatorVPP:
    """Construct and register one HybridPolicyAgent VPP."""

    agent, hybrid_params, seed, strategy = _build_hybrid_agent(
        spec,
        shared=shared,
        use_real_weather=use_real_weather,
        default_seed=default_seed,
        offset_index=offset_index,
        n_managed=n_managed,
        model=model,
        executor_override=executor_override,
        strategy_label=strategy_label,
    )
    vpp = sim.add_builtin_vpp(
        name=spec.name,
        params=hybrid_params,
        agent=agent,
        seed=seed,
        strategy=strategy,
        is_my_vpp=True,
        owner_id=owner_id,
        llm_live=shared.live,
        llm_status=shared.status,
        algorithm=algorithm,
    )
    # Every _add_hybrid_vpp agent runs the LLM strategist stack — mark it so the UI label and
    # is_llm_vpp() are accurate for both the built-in roster fleet and provisioned agents.
    vpp.llm_enabled = True
    log.info(
        "Hybrid LLM VPP %s loaded (interval=%d ticks, offset=%d, live_llm=%s)",
        spec.name,
        get_settings().llm_guidance_interval_ticks,
        round(
            offset_index
            * get_settings().llm_guidance_interval_ticks
            / max(1, n_managed)
        ),
        shared.live,
    )
    return vpp


def build_managed_brain(
    sim: Simulator,
    *,
    name: str,
    params: dict,
    persona_prompt: str | None = None,
    agent_params: dict | None = None,
    seed: int | None = None,
    model: str | None = None,
    checkpoint: str | None = None,
    algorithm: str = "ppo",
    llm_enabled: bool = True,
    online_learning: bool = True,
    use_real_weather: bool | None = None,
) -> ManagedBrain:
    """Build a managed agent without touching the simulator roster or gateway.

    ``algorithm`` is the *base* tactical algorithm (ppo / truthful / zip / gd / aa) and
    ``llm_enabled`` layers the LLM strategist on top: ppo+LLM is the classic Hybrid stack (PPO
    executor coached by the strategist), while a baseline+LLM wraps that baseline as a
    ``BaselinePolicy`` executor so the *same* guidance seam applies. Without the LLM the
    standalone agent runs directly.

    Raises ``ValueError`` / pydantic ``ValidationError`` on invalid params or agent_params.
    """
    algorithm = algorithm or "ppo"
    if algorithm not in RUNTIME_MANAGED_ALGORITHMS:
        raise ValueError(
            f"unknown managed algorithm {algorithm!r}; "
            f"choose from {list(RUNTIME_MANAGED_ALGORITHMS)}"
        )
    if not llm_enabled and (persona_prompt is not None or model is not None):
        raise ValueError("persona/model are only supported when the LLM strategist is enabled")

    real_weather = _real_pv_available() if use_real_weather is None else use_real_weather

    if llm_enabled and sim.shared_llm is None:
        # The scenario loader sets this at startup; build on demand as a defensive fallback.
        sim.shared_llm = SharedLLM.from_settings(get_settings())

    if llm_enabled:
        # Stagger this agent's strategist refresh against the managed agents already running so
        # the single shared LLM endpoint is not hit concurrently (the gate also enforces this).
        existing = len(sim.my_managed_vpps())
        persona = {"name": name, "prompt": persona_prompt} if persona_prompt else None
        if algorithm in {"ppo", "scripted"}:
            spec = AgentSpec(
                name=name,
                agent="hybrid",
                seed=seed,
                params=dict(params),
                agent_params=dict(agent_params or {}),
                persona=persona,
                executor=(
                    _managed_executor_spec(
                        online_learning=online_learning, checkpoint=checkpoint
                    )
                    if algorithm == "ppo"
                    else ExecutorSpec(kind="scripted", online_learning=False)
                ),
            )
            agent, physical_params, agent_seed, strategy = _build_hybrid_agent(
                spec,
                shared=sim.shared_llm,
                use_real_weather=real_weather,
                default_seed=seed if seed is not None else 42,
                offset_index=existing,
                n_managed=existing + 1,
                model=model,
            )
        else:
            # Wrap the classical baseline as the tactical executor the strategist coaches. Its
            # own economic params (price_ref/demand_beta/price_cap_mult) mirror the hybrid oracle
            # so the executor's `limit` and the oracle's fair price agree.
            baseline_params = _managed_agent_params(name, agent_params, algorithm)
            baseline = MANAGED_BASELINE_FACTORIES[algorithm](**baseline_params)
            oracle_params = {
                k: baseline_params[k]
                for k in ("price_ref", "demand_beta", "price_cap_mult")
                if k in baseline_params
            }
            spec = AgentSpec(
                name=name,
                agent="hybrid",
                seed=seed,
                params=dict(params),
                agent_params=oracle_params,
                persona=persona,
                executor=None,
            )
            agent, physical_params, agent_seed, strategy = _build_hybrid_agent(
                spec,
                shared=sim.shared_llm,
                use_real_weather=real_weather,
                default_seed=seed if seed is not None else 42,
                offset_index=existing,
                n_managed=existing + 1,
                model=model,
                executor_override=BaselinePolicy(baseline, use_forecast=True),
                strategy_label=f"LLM + {algorithm.upper()} (managed)",
            )
        return ManagedBrain(
            agent=agent,
            params=physical_params,
            seed=agent_seed,
            strategy=strategy,
            llm_live=sim.shared_llm.live,
            llm_status=sim.shared_llm.status,
            algorithm=algorithm,
            llm_enabled=True,
        )

    spec = AgentSpec(
        name=name,
        agent="strategy" if algorithm in {"ppo", "scripted"} else algorithm,
        seed=seed,
        params=dict(params),
        agent_params=dict(agent_params or {}),
        executor=_managed_executor_spec(
            online_learning=online_learning, checkpoint=checkpoint
        )
        if algorithm == "ppo"
        else None,
    )
    agent_seed = spec.seed if spec.seed is not None else 42
    physical_params = _build_params(spec, real_weather)
    if algorithm in {"ppo", "scripted"}:
        params_for_agent = _managed_agent_params(name, agent_params, algorithm)
        params_for_agent.setdefault("use_forecast", True)
        if get_settings().agent_character_enabled:
            params_for_agent.setdefault("character", derive_character(physical_params))
        params_for_agent["policy"] = (
            _build_executor(name, spec.executor, seed=agent_seed, auto_update=True)
            if algorithm == "ppo"
            else ScriptedStrategyPolicy(use_forecast=True)
        )
        agent = StrategyAgent(**params_for_agent)
        strategy = (
            "StrategyAgent (PPO managed)"
            if algorithm == "ppo"
            else "StrategyAgent (scripted managed)"
        )
        llm_status = "PPO executor (no LLM)" if algorithm == "ppo" else "Scripted (no LLM)"
    else:
        factory = MANAGED_BASELINE_FACTORIES[algorithm]
        params_for_agent = _managed_agent_params(name, agent_params, algorithm)
        agent = factory(**params_for_agent)
        strategy = f"{factory.__name__} (managed)"
        llm_status = "Baseline agent (no LLM)"

    return ManagedBrain(
        agent=agent,
        seed=agent_seed,
        params=physical_params,
        strategy=strategy,
        llm_live=False,
        llm_status=llm_status,
        algorithm=algorithm,
        llm_enabled=False,
    )


def provision_managed_vpp(
    sim: Simulator,
    *,
    owner_id: int,
    name: str,
    params: dict,
    persona_prompt: str | None = None,
    agent_params: dict | None = None,
    seed: int | None = None,
    model: str | None = None,
    managed_def_id: int | None = None,
    release_id: int | None = None,
    release_content_sha256: str | None = None,
    checkpoint: str | None = None,
    deployment_mode: str = "live",
    algorithm: str = "ppo",
    llm_enabled: bool = True,
    online_learning: bool = True,
    use_real_weather: bool | None = None,
) -> SimulatorVPP:
    """Construct and register a cloud-hosted managed VPP for an external user."""

    brain = build_managed_brain(
        sim,
        name=name,
        params=params,
        persona_prompt=persona_prompt,
        agent_params=agent_params,
        seed=seed,
        model=model,
        checkpoint=checkpoint,
        algorithm=algorithm,
        llm_enabled=llm_enabled,
        online_learning=online_learning,
        use_real_weather=use_real_weather,
    )
    vpp = sim.add_builtin_vpp(
        name=name,
        params=brain.params,
        agent=brain.agent,
        seed=brain.seed,
        strategy=brain.strategy,
        is_my_vpp=True,
        owner_id=owner_id,
        llm_live=brain.llm_live,
        llm_status=brain.llm_status,
        algorithm=brain.algorithm,
    )
    vpp.managed_def_id = managed_def_id
    vpp.release_id = release_id
    vpp.release_content_sha256 = release_content_sha256
    vpp.deployment_mode = deployment_mode
    vpp.llm_enabled = brain.llm_enabled
    return vpp


def validate_managed_agent_params(
    name: str, agent_params: dict | None, algorithm: str = "ppo", *, llm_enabled: bool = False
) -> None:
    """Raise ValueError if agent_params aren't accepted by the managed algorithm — the same check
    provisioning runs, exposed so callers can pre-validate before mutating a live agent."""
    _managed_agent_params(name, agent_params, algorithm or "ppo", llm_enabled=llm_enabled)


def normalize_managed_config(cfg: dict) -> tuple[str, bool]:
    """Map a managed configuration to the V1 ``(base_algorithm, llm_enabled)`` model."""
    raw = cfg.get("algorithm")
    stored = cfg.get("llm_enabled")
    if raw in RUNTIME_MANAGED_ALGORITHMS and isinstance(stored, bool):
        return str(raw), stored
    return normalize_managed_config_v0(cfg)


def apply_chat_prefs(vpp: SimulatorVPP, chat: dict | None) -> None:
    """Set a managed VPP's chatroom presence (voice/color/avatar) on the live agent.
    Plain display/prompt attributes — no re-provision, no effect on trading."""
    chat = chat or {}
    style = chat.get("style")
    color = chat.get("color")
    avatar = chat.get("avatar")
    vpp.chat_style = str(style)[:200] if style else None
    vpp.chat_color = str(color) if color else None
    vpp.chat_avatar = str(avatar)[:4] if avatar else None


def apply_external_guidance(vpp: SimulatorVPP, guidance_dict: dict, *, market_mode: str) -> dict:
    """Steer a managed VPP with externally supplied guidance (Tier A3): swap its strategist
    for an ExternalStrategist (idempotent — an existing one is reused) seeded with the
    clamped payload. Shared by the guidance API and by startup rehydration, so a restart
    neither burns platform LLM calls nor forgets the user's last guidance.

    Returns the recorded reflection entry (the clamped echo). Call under sim._lock when the
    tick loop is running. Releasing external control is the inverse:
    ``vpp.agent.strategist = ext.prior`` (the API's DELETE handler does this).
    """
    from collections import deque

    from eflux.agents.llm.strategist import (
        ExternalStrategist,
        external_guidance_from_dict,
    )

    agent = vpp.agent
    prior = getattr(agent, "strategist", None)
    if isinstance(prior, ExternalStrategist):
        ext = prior
    else:
        prior_log = getattr(prior, "reflection_log", None)
        ext = ExternalStrategist(
            prior=prior,
            client=getattr(prior, "client", None),
            # Adopt the platform strategist's log so the audit timeline stays continuous.
            reflection_log=prior_log if prior_log is not None else deque(maxlen=50),
        )
        agent.strategist = ext
    guidance, meta = external_guidance_from_dict(guidance_dict, market_mode=market_mode)
    return ext.set_guidance(guidance, meta)


def _add_mirror_vpp(
    sim: Simulator, spec: AgentSpec, *, use_real_weather: bool, default_seed: int
) -> None:
    """Add the strategist-less PPO twin of a hybrid entry: a StrategyAgent with the *same*
    executor (a fresh policy instance, warm-started from the same checkpoint), params, and
    seed — so it sees the identical DER trajectory and only the LLM meta-control distinguishes
    it. Name suffix '-ppo-mirror'. The twin learns online too (auto_update), just without any
    LLM steer, isolating exactly one variable for the A/B."""
    seed = spec.seed if spec.seed is not None else default_seed
    name = f"{spec.name}-ppo-mirror"
    policy = _build_executor(name, spec.executor, seed=seed, auto_update=True)
    # agent_params validated for HybridPolicyAgent share StrategyAgent's fields (price_ref,
    # demand_beta, min_qty, price_cap_mult) — pass them straight through, pinning price_ref to
    # the PPO normalization scale so the twin's obs match its checkpoint.
    agent_params = dict(spec.agent_params)
    agent_params.setdefault("price_ref", _ppo_price_ref())
    agent_params.setdefault("use_forecast", True)
    if get_settings().agent_character_enabled:
        agent_params.setdefault("character", derive_character(_build_params(spec, use_real_weather)))
    agent = StrategyAgent(**agent_params, policy=policy)
    sim.add_builtin_vpp(
        name=name,
        params=_build_params(spec, use_real_weather),
        agent=agent,
        seed=seed,
        strategy="StrategyAgent (PPO mirror)",
        mirror_of=spec.name,
    )
    log.info("PPO mirror VPP %s loaded (twin of %s, seed=%d)", name, spec.name, seed)
