"""Built-in scenario loading — roster comes from a YAML file (scenarios/default.yaml).

Every entry is validated as an AgentSpec (see simulator/agent_spec.py — the same
schema external participants integrate against). Strategy kinds: zi | truthful |
gas | strategy | hybrid. Hybrid LLM-managed entries share one SharedLLM connection
and get evenly staggered strategist refresh offsets so the single slow endpoint
is never hit concurrently. `reflective` is kept as a legacy alias for hybrid.
"""

from __future__ import annotations

import logging
import os
import random
from decimal import Decimal
from pathlib import Path

import yaml

from eflux.agents.aa_agent import AAAgent
from eflux.agents.base import BaseAgent
from eflux.agents.gas import GasGeneratorAgent
from eflux.agents.gd_agent import GDAgent
from eflux.agents.hybrid import HybridPolicyAgent, StrategyAgent
from eflux.agents.reflective.pool import SharedLLM
from eflux.agents.reflective.strategist import LLMStrategist
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.zi import ZIAgent
from eflux.agents.zip_agent import ZIPAgent
from eflux.config import PROJECT_ROOT, get_settings
from eflux.data.caiso_reference import caiso_reference_price
from eflux.simulator.agent_spec import AgentSpec, validate_vpp_params
from eflux.simulator.runner import Simulator, SimulatorVPP
from eflux.vpp.base import VPPParams

log = logging.getLogger(__name__)

AGENT_FACTORIES: dict[str, type[BaseAgent]] = {
    "zi": ZIAgent,
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

# Cost-based agents whose price_ref is re-based to the CAISO reference + jittered for cost
# diversification (LLM/gas/strategy-with-pinned-ref are handled elsewhere).
_COST_DIVERSIFIED_KINDS = frozenset({"zi", "truthful", "strategy", "zip", "gd", "aa"})

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
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("vpps") or []
    if not entries:
        raise ValueError(f"Scenario file {path} contains no 'vpps' entries")

    specs = [AgentSpec.model_validate(entry) for entry in entries]
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise ValueError(f"Scenario file {path}: duplicate VPP name {spec.name!r}")
        seen.add(spec.name)

    use_real_weather = _real_pv_available()
    managed_specs = [s for s in specs if s.agent in ("hybrid", "reflective")]
    # Build the shared LLM connection unconditionally and retain it on the simulator, so the
    # API can provision managed agents at runtime even when the roster declares none.
    shared = SharedLLM.from_settings(settings)
    sim.shared_llm = shared

    counts: dict[str, int] = {}
    managed_index = 0
    for i, spec in enumerate(specs):
        if spec.agent in ("hybrid", "reflective"):
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
                agent_params["policy"] = _build_executor(
                    spec.name, spec.executor, seed=seed, auto_update=True
                )
            else:
                agent_params = _diversify_cost(spec, seed, settings.price_ref_jitter_frac, factory)
            sim.add_builtin_vpp(
                name=spec.name,
                params=_build_params(spec, use_real_weather),
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

    # Back-compat: a roster without LLM-managed entries still gets the demo LLM VPP.
    if not managed_specs:
        load_my_llm_vpp(sim)

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
) -> SimulatorVPP:
    """Add one HybridPolicyAgent VPP sharing the SharedLLM connection.

    Offsets are spread evenly over the reflection interval (index-based —
    deterministic, no collisions) so calls to the single endpoint stagger
    instead of landing on the same tick.
    """
    settings = get_settings()
    interval = settings.reflective_interval_ticks
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
    agent = HybridPolicyAgent(
        **agent_params,
        executor=_build_executor(
            spec.name, spec.executor, seed=seed, auto_update=not settings.online_update_async
        ),
        strategist=strategist,
        refresh_every_n_ticks=interval,
        refresh_offset_ticks=offset,
        persona_prompt=spec.persona.prompt if spec.persona else None,
    )
    vpp = sim.add_builtin_vpp(
        name=spec.name,
        params=_build_params(spec, use_real_weather),
        agent=agent,
        seed=seed,
        strategy=f"HybridPolicyAgent ({model or shared.default_model or shared.strategy_suffix})",
        is_my_vpp=True,
        owner_id=owner_id,
        llm_live=shared.live,
        llm_status=shared.status,
    )
    log.info(
        "Hybrid LLM VPP %s loaded (interval=%d ticks, offset=%d, live_llm=%s)",
        spec.name,
        interval,
        offset,
        shared.live,
    )
    return vpp


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
) -> SimulatorVPP:
    """Provision a cloud-hosted managed (LLM-steered HybridPolicyAgent) VPP for an external
    user at runtime — the same construction and validation as the roster's hybrid entries,
    but attributed to ``owner_id`` so ``my_managed_vpps(owner_id)`` scopes it to that user.
    The agent is then driven autonomously by the simulator tick loop.

    Raises ``ValueError`` / pydantic ``ValidationError`` on invalid params or agent_params
    (the API layer maps these to HTTP 422). Nothing is added to the simulator on failure.
    """
    if sim.shared_llm is None:
        # The scenario loader sets this at startup; build on demand as a defensive fallback.
        sim.shared_llm = SharedLLM.from_settings(get_settings())
    spec = AgentSpec(
        name=name,
        agent="hybrid",
        seed=seed,
        params=dict(params),
        agent_params=dict(agent_params or {}),
        persona={"name": name, "prompt": persona_prompt} if persona_prompt else None,
    )
    # Stagger this agent's strategist refresh against the managed agents already running so
    # the single shared LLM endpoint is not hit concurrently (the gate also enforces this).
    existing = len(sim.my_managed_vpps())
    vpp = _add_hybrid_vpp(
        sim,
        spec,
        shared=sim.shared_llm,
        use_real_weather=_real_pv_available(),
        default_seed=seed if seed is not None else 42,
        offset_index=existing,
        n_managed=existing + 1,
        owner_id=owner_id,
        model=model,
    )
    vpp.managed_def_id = managed_def_id
    return vpp


def validate_managed_agent_params(name: str, agent_params: dict | None) -> None:
    """Raise ValueError if agent_params aren't accepted by HybridPolicyAgent — the same check
    provisioning runs, exposed so callers can pre-validate before mutating a live agent."""
    _validate_agent_params(name, dict(agent_params or {}), HybridPolicyAgent)


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

    from eflux.agents.reflective.strategist import (
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


def load_my_llm_vpp(sim: Simulator) -> None:
    """Back-compat shim: add the demo LLM VPP when the roster declares none."""
    spec = AgentSpec(
        name="my-llm-vpp",
        agent="hybrid",
        seed=77,
        params={
            "pv_kw_peak": 5.0,
            "battery_kwh": 15.0,
            "battery_kw_max": 4.0,
            "load_kw_base": 2.5,
            "markup_floor": 0.4,
        },
    )
    shared = SharedLLM.from_settings(get_settings())
    _add_hybrid_vpp(
        sim,
        spec,
        shared=shared,
        use_real_weather=_real_pv_available(),
        default_seed=77,
        offset_index=0,
        n_managed=1,
    )
