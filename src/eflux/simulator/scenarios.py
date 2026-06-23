"""Built-in scenario loading — roster comes from a YAML file (scenarios/default.yaml).

Every entry is validated as an AgentSpec (see simulator/agent_spec.py — the same
schema external participants integrate against). Strategy kinds: zi | truthful |
gas | reflective. Reflective (LLM-steered) entries share one SharedLLM connection
and get evenly staggered reflection offsets so the single slow endpoint is never
hit concurrently.
"""

from __future__ import annotations

import logging
import os
import random
from decimal import Decimal
from pathlib import Path

import yaml

from eflux.agents.base import BaseAgent
from eflux.agents.gas import GasGeneratorAgent
from eflux.agents.hybrid import HybridPolicyAgent, StrategyAgent
from eflux.agents.reflective import ReflectiveAgent
from eflux.agents.reflective.memory import AgentMemory, slug
from eflux.agents.reflective.pool import SharedLLM
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.zi import ZIAgent
from eflux.config import PROJECT_ROOT, get_settings
from eflux.simulator.agent_spec import AgentSpec, validate_vpp_params
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams

log = logging.getLogger(__name__)

AGENT_FACTORIES: dict[str, type[BaseAgent]] = {
    "zi": ZIAgent,
    "truthful": TruthfulAgent,
    "gas": GasGeneratorAgent,
    "strategy": StrategyAgent,
    "hybrid": HybridPolicyAgent,
}

# Dataclass fields that hold injected policy/strategist objects, never YAML kwargs.
_INJECTED_FIELDS = frozenset({"policy", "executor", "strategist", "fallback"})


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


def _real_pv_available() -> bool:
    """True iff pvlib is installed AND user hasn't opted out via EFLUX_PV_PHYSICAL=false."""
    if os.environ.get("EFLUX_PV_PHYSICAL", "auto").lower() in ("false", "0", "off"):
        return False
    try:
        import pvlib  # noqa: F401
        return True
    except ImportError:
        return False


def load_default_scenario(sim: Simulator) -> None:
    """Load and validate the YAML roster; wire reflective entries to the shared LLM."""
    settings = get_settings()
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
    reflective_specs = [s for s in specs if s.agent == "reflective"]
    shared = SharedLLM.from_settings(settings) if reflective_specs else None

    counts: dict[str, int] = {}
    reflective_index = 0
    for i, spec in enumerate(specs):
        if spec.agent == "reflective":
            _add_reflective_vpp(
                sim,
                spec,
                shared=shared,  # type: ignore[arg-type]  # set when reflective_specs is non-empty
                use_real_weather=use_real_weather,
                default_seed=42 + i,
                offset_index=reflective_index,
                n_reflective=len(reflective_specs),
            )
            reflective_index += 1
        else:
            factory = AGENT_FACTORIES[spec.agent]
            _validate_agent_params(spec.name, spec.agent_params, factory)
            seed = spec.seed if spec.seed is not None else 42 + i
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

    # Back-compat: a roster without reflective entries still gets the demo LLM VPP.
    if not reflective_specs:
        load_my_llm_vpp(sim)

    # Optional: if a PPO checkpoint is configured, add one more VPP driven by it.
    ckpt = os.environ.get("EFLUX_PPO_CHECKPOINT")
    if ckpt:
        load_ppo_scenario(sim, ckpt)

    log.info(
        "Default scenario ready: %d ordinary agents + %d LLM agents",
        len(specs) - len(reflective_specs),
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
    levels out into a band. Reflective (LLM) agents never reach here — they are
    handled on the other branch — so the LLM fleet is excluded by construction.
    Gas has no price_ref (its cost is per-VPP already) and is left untouched, as
    is any agent whose roster entry pins price_ref explicitly.
    """
    agent_params = dict(spec.agent_params)
    if (
        jitter_frac <= 0
        or spec.agent not in ("zi", "truthful", "strategy")
        or "price_ref" in agent_params
    ):
        return agent_params
    field = factory.__dataclass_fields__.get("price_ref")
    base = float(field.default) if field is not None else 50.0  # type: ignore[union-attr]
    rng = random.Random(f"price_ref::{spec.name}::{seed}")
    price_ref = round(base * (1.0 + rng.uniform(-jitter_frac, jitter_frac)), 4)
    agent_params["price_ref"] = Decimal(str(price_ref))
    return agent_params


def _build_params(spec: AgentSpec, use_real_weather: bool) -> VPPParams:
    params_dict = dict(spec.params)
    if not use_real_weather:
        # Strip site coords → no Open-Meteo fetch, stub PV + wind models.
        params_dict.pop("pv_lat", None)
        params_dict.pop("pv_lon", None)
    # Build from the *validated/coerced* dict, not the raw YAML values —
    # from_dict alone would happily store battery_kwh: "12" (a string) and
    # blow up mid-run on the first arithmetic touch.
    return VPPParams.from_dict(validate_vpp_params(params_dict))


def _add_reflective_vpp(
    sim: Simulator,
    spec: AgentSpec,
    *,
    shared: SharedLLM,
    use_real_weather: bool,
    default_seed: int,
    offset_index: int,
    n_reflective: int,
) -> None:
    """Add one LLM-steered VPP sharing the SharedLLM connection.

    Offsets are spread evenly over the reflection interval (index-based —
    deterministic, no collisions) so calls to the single endpoint stagger
    instead of landing on the same tick.
    """
    settings = get_settings()
    interval = settings.reflective_interval_ticks
    offset = round(offset_index * interval / max(1, n_reflective))
    memory_dir = Path(settings.agent_memory_dir)
    if not memory_dir.is_absolute():
        memory_dir = PROJECT_ROOT / memory_dir
    memory = AgentMemory(memory_dir / f"{slug(spec.name)}.jsonl")
    loaded = memory.load()
    if loaded:
        log.info("Reflective VPP %s recalled %d memory records", spec.name, loaded)
    _validate_agent_params(spec.name, spec.agent_params, TruthfulAgent)
    agent = ReflectiveAgent(
        llm_client=shared.client,
        inner=TruthfulAgent(**spec.agent_params),
        reflect_every_n_ticks=interval,
        reflect_offset_ticks=offset,
        llm_gate=shared.gate,
        persona_prompt=spec.persona.prompt if spec.persona else None,
        memory=memory,
    )
    sim.add_builtin_vpp(
        name=spec.name,
        params=_build_params(spec, use_real_weather),
        agent=agent,
        seed=spec.seed if spec.seed is not None else default_seed,
        strategy=f"ReflectiveAgent ({shared.strategy_suffix})",
        is_my_vpp=True,
        llm_live=shared.live,
        llm_status=shared.status,
    )
    log.info(
        "Reflective VPP %s loaded (interval=%d ticks, offset=%d, live_llm=%s)",
        spec.name,
        interval,
        offset,
        shared.live,
    )


def load_my_llm_vpp(sim: Simulator) -> None:
    """Back-compat shim: add the demo LLM VPP when the roster declares none."""
    spec = AgentSpec(
        name="my-llm-vpp",
        agent="reflective",
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
    _add_reflective_vpp(
        sim,
        spec,
        shared=shared,
        use_real_weather=_real_pv_available(),
        default_seed=77,
        offset_index=0,
        n_reflective=1,
    )


def load_ppo_scenario(sim: Simulator, checkpoint_path: str) -> None:
    """Add a PPO-driven VPP to the simulator. Skipped (with a warning) if the 'ai'
    extras are missing or the checkpoint cannot be loaded — a bad EFLUX_PPO_CHECKPOINT
    must not take down the whole app at startup.

    EFLUX_PPO_ENV selects the policy kind: "single" (default, the legacy 3-float
    action agent) or "primitive" (a StrategyAgent driven by a learned policy over the
    structured StrategyAction space — must match how the checkpoint was trained)."""
    ppo_env = os.environ.get("EFLUX_PPO_ENV", "single").lower()
    try:
        if ppo_env == "primitive":
            from eflux.agents.ppo.primitive_agent import build_ppo_primitive_agent
        else:
            from eflux.agents.ppo.agent import PPOAgent
    except ImportError as e:
        log.warning("PPO checkpoint %s configured but 'ai' extras not installed (%s) — skipping", checkpoint_path, e)
        return
    # The policy wrapper loads lazily (Ray init is expensive), so a bad path
    # would otherwise surface as an inference error on every tick instead of
    # one clear startup message.
    if not Path(checkpoint_path).exists():
        log.warning("PPO checkpoint %s does not exist — skipping the PPO VPP", checkpoint_path)
        return
    try:
        agent = (
            build_ppo_primitive_agent(checkpoint_path)
            if ppo_env == "primitive"
            else PPOAgent(checkpoint_path=checkpoint_path)
        )
    except Exception:
        log.exception("PPO checkpoint %s failed to load — skipping the PPO VPP", checkpoint_path)
        return
    params = VPPParams(pv_kw_peak=6.0, battery_kwh=20.0, battery_kw_max=5.0, load_kw_base=2.0)
    sim.add_builtin_vpp(
        name=f"builtin-ppo-{os.path.basename(checkpoint_path)}",
        params=params,
        agent=agent,
        seed=99,
    )
    log.info("PPO VPP loaded from checkpoint %s", checkpoint_path)
