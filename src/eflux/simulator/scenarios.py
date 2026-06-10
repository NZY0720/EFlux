"""Built-in scenario loading — roster comes from a YAML file (scenarios/default.yaml).

Each entry maps to one built-in VPP: a name, an agent kind (zi | truthful | gas)
and a VPPParams dict. The LLM-managed my-llm-vpp is always appended in code
because it needs the LLM client wiring from settings.
"""

from __future__ import annotations

import logging

import os
from pathlib import Path

import httpx
import yaml

from eflux.agents.base import BaseAgent
from eflux.agents.gas import GasGeneratorAgent
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.zi import ZIAgent
from eflux.config import PROJECT_ROOT, get_settings
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams

log = logging.getLogger(__name__)

AGENT_FACTORIES: dict[str, type[BaseAgent]] = {
    "zi": ZIAgent,
    "truthful": TruthfulAgent,
    "gas": GasGeneratorAgent,
}


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
    """Load the YAML roster, then append the user-visible LLM-managed VPP."""
    settings = get_settings()
    path = Path(settings.scenario_file)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("vpps") or []
    if not entries:
        raise ValueError(f"Scenario file {path} contains no 'vpps' entries")

    use_real_weather = _real_pv_available()
    counts: dict[str, int] = {}
    for i, entry in enumerate(entries):
        name = entry["name"]
        agent_kind = str(entry.get("agent", "zi")).lower()
        factory = AGENT_FACTORIES.get(agent_kind)
        if factory is None:
            raise ValueError(f"Scenario entry {name!r}: unknown agent kind {agent_kind!r}")
        params_dict = dict(entry.get("params") or {})
        if not use_real_weather:
            # Strip site coords → no Open-Meteo fetch, stub PV + wind models.
            params_dict.pop("pv_lat", None)
            params_dict.pop("pv_lon", None)
        params = VPPParams.from_dict(params_dict)
        sim.add_builtin_vpp(
            name=name,
            params=params,
            agent=factory(),
            seed=int(entry.get("seed", 42 + i)),
        )
        counts[agent_kind] = counts.get(agent_kind, 0) + 1

    log.info(
        "Scenario %s: %d VPPs loaded (%s, real_weather=%s)",
        path.name,
        len(entries),
        ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        use_real_weather,
    )

    load_my_llm_vpp(sim)

    # Optional: if a PPO checkpoint is configured, add one more VPP driven by it.
    ckpt = os.environ.get("EFLUX_PPO_CHECKPOINT")
    if ckpt:
        load_ppo_scenario(sim, ckpt)

    log.info(
        "Default scenario ready: %d ordinary agents + %d LLM agent",
        len(entries),
        len(sim.my_managed_vpps()),
    )


def load_my_llm_vpp(sim: Simulator) -> None:
    """Add the single LLM-managed VPP that appears on the My VPPs page.

    The agent is always present for the demo. It calls the LLM only when reflective
    mode is enabled and the OpenAI-compatible endpoint is fully configured.
    """
    from eflux.config import get_settings

    settings = get_settings()
    from eflux.agents.reflective import ReflectiveAgent
    from eflux.agents.reflective.llm_client import LLMClient
    from eflux.agents.truthful import TruthfulAgent

    api_key = settings.llm_api_key
    client = None
    strategy = "ReflectiveAgent (offline fallback)"
    llm_status = "offline fallback until LLM is configured"
    if settings.reflective_enabled and api_key and settings.llm_base_url and settings.llm_model:
        ok, detail = _validate_llm_connection(
            base_url=settings.llm_base_url,
            api_key=api_key,
            model=settings.llm_model,
        )
        if ok:
            client = LLMClient(
                base_url=settings.llm_base_url,
                api_key=api_key,
                model=settings.llm_model,
                timeout_sec=settings.llm_timeout_sec,
            )
            strategy = f"ReflectiveAgent ({settings.llm_provider}:{settings.llm_model})"
            llm_status = f"live LLM reflection via {settings.llm_base_url}"
        else:
            llm_status = f"offline fallback: {detail}"
            log.warning("My LLM VPP connection check failed: %s", detail)
    else:
        missing = []
        if not settings.reflective_enabled:
            missing.append("EFLUX_REFLECTIVE_ENABLED=false")
        if not api_key:
            missing.append("missing key.txt")
        if not settings.llm_base_url:
            missing.append("missing EFLUX_LLM_BASE_URL")
        if not settings.llm_model:
            missing.append("missing EFLUX_LLM_MODEL")
        llm_status = "offline fallback: " + ", ".join(missing)
        log.warning(
            "My LLM VPP loaded without live LLM calls (enabled=%s key=%s base_url=%s model=%s)",
            settings.reflective_enabled,
            bool(api_key),
            bool(settings.llm_base_url),
            bool(settings.llm_model),
        )

    agent = ReflectiveAgent(
        llm_client=client,
        inner=TruthfulAgent(),
        reflect_every_n_ticks=settings.reflective_interval_ticks,
    )
    sim.add_builtin_vpp(
        name="my-llm-vpp",
        params=VPPParams(pv_kw_peak=5.0, battery_kwh=15.0, battery_kw_max=4.0, load_kw_base=2.5, markup_floor=0.4),
        agent=agent,
        seed=77,
        strategy=strategy,
        is_my_vpp=True,
        llm_live=client is not None,
        llm_status=llm_status,
    )
    log.info("My LLM VPP loaded (interval=%d ticks, live_llm=%s)", settings.reflective_interval_ticks, client is not None)


def _validate_llm_connection(*, base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with only: ok"}],
                "max_completion_tokens": 8,
                "temperature": 0.2,
                "stream": False,
            },
            timeout=15.0,
        )
        if resp.status_code >= 400:
            try:
                data = resp.json()
                message = data.get("error", {}).get("message") or resp.text[:160]
            except Exception:
                message = resp.text[:160]
            return False, f"{resp.status_code} {message}"
        data = resp.json()
        data["choices"][0]["message"]["content"]
        return True, "ok"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def load_ppo_scenario(sim: Simulator, checkpoint_path: str) -> None:
    """Add a PPO-driven VPP to the simulator. Skipped silently if 'ai' extras missing."""
    try:
        from eflux.agents.ppo.agent import PPOAgent
    except ImportError as e:
        log.warning("PPO checkpoint %s configured but 'ai' extras not installed (%s) — skipping", checkpoint_path, e)
        return
    params = VPPParams(pv_kw_peak=6.0, battery_kwh=20.0, battery_kw_max=5.0, load_kw_base=2.0)
    sim.add_builtin_vpp(
        name=f"builtin-ppo-{os.path.basename(checkpoint_path)}",
        params=params,
        agent=PPOAgent(checkpoint_path=checkpoint_path),
        seed=99,
    )
    log.info("PPO VPP loaded from checkpoint %s", checkpoint_path)
