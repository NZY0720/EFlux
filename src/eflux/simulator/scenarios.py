"""Built-in scenarios for dev/demo. Phase 4 will replace with a YAML loader."""

from __future__ import annotations

import logging

import os
import httpx

from eflux.agents.base import BaseAgent
from eflux.agents.truthful import TruthfulAgent
from eflux.agents.zi import ZIAgent
from eflux.simulator.runner import Simulator
from eflux.vpp.base import VPPParams

log = logging.getLogger(__name__)


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
    """10 ordinary built-in VPPs plus one user-visible LLM-managed VPP.

    The solar VPP defaults to HKU rooftop coords if pvlib is installed, so the simulator
    drives PV from real Open-Meteo data instead of the diurnal stub.
    """
    use_real_pv = _real_pv_available()
    if use_real_pv:
        solar_params = VPPParams(
            pv_kw_peak=8.0, battery_kwh=15.0, battery_kw_max=4.0, load_kw_base=1.2,
            pv_lat=22.28, pv_lon=114.13, pv_tilt=22.0, pv_azimuth=180.0,
        )
    else:
        solar_params = VPPParams(pv_kw_peak=8.0, battery_kwh=15.0, battery_kw_max=4.0, load_kw_base=1.2)

    # Truthful sellers ask markup_floor * price_ref for PV surplus. With the
    # param default (0.0) that floors at 0.0001 — whenever the bid side of the
    # book is momentarily empty, the ask rests there and gets picked off for
    # free. A 0.4 floor (= 20 with price_ref 50) keeps asks competitive against
    # ZI quotes (uniform 25–75) without giving energy away.
    truthful_floor = 0.4
    presets: list[tuple[str, VPPParams, BaseAgent]] = [
        ("ordinary-zi-solar-01", solar_params, ZIAgent()),
        ("ordinary-truthful-batt-02", VPPParams(pv_kw_peak=4.0, battery_kwh=30.0, battery_kw_max=8.0, load_kw_base=2.0, markup_floor=truthful_floor), TruthfulAgent()),
        ("ordinary-zi-load-03", VPPParams(pv_kw_peak=0.5, battery_kwh=5.0, battery_kw_max=2.0, load_kw_base=5.0), ZIAgent()),
        ("ordinary-zi-rooftop-04", VPPParams(pv_kw_peak=6.0, battery_kwh=12.0, battery_kw_max=3.0, load_kw_base=1.5), ZIAgent()),
        ("ordinary-truthful-flex-05", VPPParams(pv_kw_peak=3.0, battery_kwh=18.0, battery_kw_max=5.0, load_kw_base=3.0, markup_floor=truthful_floor), TruthfulAgent()),
        ("ordinary-zi-battery-06", VPPParams(pv_kw_peak=2.0, battery_kwh=28.0, battery_kw_max=8.0, load_kw_base=2.5), ZIAgent()),
        ("ordinary-truthful-solar-07", VPPParams(pv_kw_peak=7.0, battery_kwh=8.0, battery_kw_max=3.0, load_kw_base=1.0, markup_floor=truthful_floor), TruthfulAgent()),
        ("ordinary-zi-commercial-load-08", VPPParams(pv_kw_peak=1.0, battery_kwh=10.0, battery_kw_max=4.0, load_kw_base=6.0), ZIAgent()),
        ("ordinary-truthful-mixed-09", VPPParams(pv_kw_peak=5.0, battery_kwh=20.0, battery_kw_max=6.0, load_kw_base=2.8, markup_floor=truthful_floor), TruthfulAgent()),
        ("ordinary-zi-evening-load-10", VPPParams(pv_kw_peak=1.5, battery_kwh=6.0, battery_kw_max=2.5, load_kw_base=4.5), ZIAgent()),
    ]
    for i, (name, params, agent) in enumerate(presets):
        sim.add_builtin_vpp(name=name, params=params, agent=agent, seed=42 + i)
    log.info("Default scenario: %d ordinary VPPs loaded (mixed ZI + Truthful, real_pv=%s)", len(presets), use_real_pv)

    load_my_llm_vpp(sim)

    # Optional: if a PPO checkpoint is configured, add a 4th VPP driven by it.
    ckpt = os.environ.get("EFLUX_PPO_CHECKPOINT")
    if ckpt:
        load_ppo_scenario(sim, ckpt)

    log.info("Default scenario ready: %d ordinary agents + %d LLM agent", len(presets), len(sim.my_managed_vpps()))


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
