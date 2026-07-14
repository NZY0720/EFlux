"""Deterministic official managed-track scoring on the offline benchmark harness."""

from __future__ import annotations

import math
import queue
import threading
from dataclasses import asdict, dataclass
from decimal import Decimal
from statistics import median
from typing import Any

from eflux.agents.base import BaseAgent
from eflux.agents.bench.run import BENCH_EPOCH, measure_episode, run_episode
from eflux.agents.bench.scenarios import test_slot_params
from eflux.agents.decision import AgentDecision
from eflux.bridge.bus import InMemoryBus
from eflux.config import get_settings
from eflux.evaluation.rules import OFFICIAL_DECIDE_DEADLINE_MS
from eflux.simulator.agent_spec import validate_vpp_params
from eflux.simulator.runner import Simulator
from eflux.simulator.scenarios import provision_managed_vpp
from eflux.stats.score import compute_score
from eflux.vpp.base import VPPParams

OFFICIAL_PRICE_REF = 50.0
_IQR_FLOOR = 1e-9


@dataclass(frozen=True)
class SeedScore:
    status: str
    score: float
    metrics: dict[str, Any]


class _GuardedSubmissionAgent(BaseAgent):
    """Convert participant code faults into a durable failure plus NOOP actions."""

    def __init__(self, agent: BaseAgent | None, failure_reason: str | None = None) -> None:
        self.agent = agent
        self.failure_reason = failure_reason

    def __getattr__(self, name: str) -> Any:
        if self.agent is None:
            raise AttributeError(name)
        return getattr(self.agent, name)

    def decide(self, ctx) -> AgentDecision:
        if self.agent is None or self.failure_reason is not None:
            return AgentDecision.hold("submission is unavailable")
        result: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                result.put((True, self.agent.decide(ctx)))
            except BaseException as exc:
                result.put((False, exc))

        # Python cannot safely kill a participant thread. On timeout it is left as
        # a daemon, while the episode records a decision failure and returns promptly.
        threading.Thread(target=invoke, name="official-decide", daemon=True).start()
        try:
            ok, value = result.get(timeout=OFFICIAL_DECIDE_DEADLINE_MS / 1000)
        except queue.Empty:
            self.failure_reason = f"decision deadline exceeded ({OFFICIAL_DECIDE_DEADLINE_MS}ms)"
            return AgentDecision.hold("submission decision timed out")
        try:
            if not ok:
                assert isinstance(value, BaseException)
                self.failure_reason = f"{type(value).__name__}: {value}"
                return AgentDecision.hold("submission decision failed")
            decision = value
            if not isinstance(decision, AgentDecision):
                raise TypeError("agent returned data outside the AgentDecision protocol")
            return decision
        except Exception as exc:
            self.failure_reason = f"{type(exc).__name__}: {exc}"
            return AgentDecision.hold("submission decision failed")


def _percentile(values: list[float], q: float) -> float:
    """Deterministic linear percentile (the common n-1 interpolation definition)."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot take a percentile of an empty roster")
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _episode_shape(seed_hours: float, window_sec: float) -> tuple[int, float]:
    if seed_hours <= 0:
        raise ValueError("seed_hours must be positive")
    if window_sec <= 0:
        raise ValueError("window_sec must be positive")
    # The synchronous harness settles whole delivery products. Use the largest
    # whole-product action window that does not exceed ``window_sec``; when the
    # requested window is shorter than one product, one product is the minimum
    # representable episode step.
    product_sec = get_settings().delivery_interval_sec
    products_per_tick = max(1, int(window_sec // product_sec))
    tick_sec = products_per_tick * product_sec
    n_ticks = max(1, math.ceil(seed_hours * 3600.0 / tick_sec))
    return n_ticks, tick_sec / 3600.0


def _submission_endowment(payload: dict[str, Any]) -> VPPParams:
    raw = payload.get("endowment")
    if raw is None:
        preset = payload.get("preset")
        if isinstance(preset, dict):
            raw = preset
        elif preset is None or (isinstance(preset, str) and preset.strip()):
            # Season 0 stores the display preset label but has one canonical managed
            # evaluation endowment. Explicit custom endowments take the branch above.
            raw = test_slot_params().to_dict()
        else:
            raise ValueError(f"unknown evaluation preset: {preset!r}")
    if not isinstance(raw, dict):
        raise ValueError("submission endowment must be an object")

    params = dict(raw)
    risk = payload.get("risk")
    if isinstance(risk, (int, float)) and not isinstance(risk, bool):
        params["risk_aversion"] = float(risk)
    elif isinstance(risk, dict) and "risk_aversion" in risk:
        params["risk_aversion"] = risk["risk_aversion"]
    validated = validate_vpp_params(params)
    # Official episodes are synthetic/offline even if a submitted endowment contains
    # live-site coordinates from its managed deployment.
    validated["pv_lat"] = None
    validated["pv_lon"] = None
    return VPPParams.from_dict(validated)


def _make_submission_agent(
    payload: dict[str, Any], *, seed: int, endowment: VPPParams
) -> BaseAgent:
    """Build through managed provisioning with every non-deterministic facility disabled."""
    agent_params = dict(payload.get("agent_params") or {})
    risk = payload.get("risk")
    if isinstance(risk, dict) and isinstance(risk.get("agent_params"), dict):
        agent_params.update(risk["agent_params"])
    # Pin the valuation reference so the live CAISO configuration can never trigger a
    # network-backed reference lookup in the official offline worker.
    agent_params["price_ref"] = Decimal(str(OFFICIAL_PRICE_REF))

    scratch = Simulator(bus=InMemoryBus(), sim_epoch=BENCH_EPOCH)
    provisioned = provision_managed_vpp(
        scratch,
        owner_id=0,
        name="official-submission",
        params=endowment.to_dict(),
        agent_params=agent_params,
        seed=seed,
        algorithm=str(payload.get("algorithm") or "ppo"),
        llm_enabled=False,
        online_learning=False,
        use_real_weather=False,
    )
    if provisioned.llm_enabled:
        raise RuntimeError("official evaluation provisioned an LLM-enabled agent")
    return provisioned.agent


def _raw_score(episode_metrics: Any, endowment: VPPParams, seed_hours: float) -> float:
    return compute_score(
        episode_metrics.mark_to_market,
        endowment.to_dict(),
        OFFICIAL_PRICE_REF,
        seed_hours,
    )


def score_seed(
    payload: dict[str, Any],
    seed: int,
    *,
    seed_hours: float,
    window_sec: float,
) -> SeedScore:
    """Score one hidden seed; benchmark errors deliberately propagate as infrastructure."""
    endowment = _submission_endowment(payload)
    n_ticks, tick_h = _episode_shape(seed_hours, window_sec)
    try:
        submission_agent = _make_submission_agent(payload, seed=seed, endowment=endowment)
        guarded = _GuardedSubmissionAgent(submission_agent)
    except (TypeError, ValueError) as exc:
        guarded = _GuardedSubmissionAgent(None, f"{type(exc).__name__}: {exc}")

    sim, participant_vpp = run_episode(
        lambda: guarded,
        n_ticks=n_ticks,
        tick_h=tick_h,
        episode_seed=seed,
        candidate_params=endowment,
    )
    participant = measure_episode("submission", sim, participant_vpp, n_ticks)
    roster_raw: dict[str, float] = {}
    for vpp in sim.vpps.values():
        if vpp is participant_vpp:
            continue
        episode_metrics = measure_episode(vpp.name, sim, vpp, n_ticks)
        roster_raw[vpp.name] = _raw_score(episode_metrics, vpp.params, seed_hours)
    raw_values = list(roster_raw.values())
    roster_median = float(median(raw_values))
    roster_iqr = _percentile(raw_values, 0.75) - _percentile(raw_values, 0.25)
    denominator = max(roster_iqr, _IQR_FLOOR)
    roster_p10 = _percentile(raw_values, 0.10)
    floor_score = (roster_p10 - roster_median) / denominator
    agent_raw = _raw_score(participant, endowment, seed_hours)
    normalized = (agent_raw - roster_median) / denominator
    participant_metrics = asdict(participant)
    common_metrics: dict[str, Any] = {
        "n_ticks": n_ticks,
        "tick_h": tick_h,
        "roster_raw": roster_raw,
        "roster_median": roster_median,
        "roster_iqr": roster_iqr,
        "roster_p10": roster_p10,
        "floor_score": floor_score,
        "agent_raw": agent_raw,
        "normalized": normalized,
        "episode": participant_metrics,
    }

    if guarded.failure_reason is not None:
        return SeedScore(
            status="participant_failure",
            score=floor_score,
            metrics={**common_metrics, "reason": guarded.failure_reason},
        )

    metrics = dict(common_metrics)
    if participant.risk_rejections:
        metrics["reason"] = f"{participant.risk_rejections} invalid action(s) rejected"
        return SeedScore(status="participant_failure", score=floor_score, metrics=metrics)
    return SeedScore(status="ok", score=normalized, metrics=metrics)
