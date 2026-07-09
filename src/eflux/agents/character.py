"""Per-agent character — endowment-driven behavioural personality.

Today two agents with the same algorithm behave identically modulo their live state:
endowment (battery / PV / load / gas) only scales order *sizes*, and "persona" is free
text seen by the LLM alone. `Character` fixes that: a small, immutable personality that is
auto-derived from an agent's endowment (or set explicitly) and applied as a light,
always-on modulation of the tactical action — so a battery-heavy arbitrageur presses
harder and swings its SOC wide, while a load-heavy consumer stays cautious and keeps the
battery charged for its load.

It is applied uniformly *after* the executor (scripted / baseline / PPO / LLM-guided)
produces its `StrategyAction`, so every agent type is differentiated without touching the
PPO obs/action encoding. The NEUTRAL default is a strict identity, so agents constructed
without a character behave exactly as before (parity preserved); the scenario loader and
managed-provisioning derive a real character for live agents.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from eflux.agents.strategy.schema import StrategyAction

# Archetypes, in the priority order the classifier tests them.
ARCHETYPES = ("dispatchable", "arbitrageur", "producer", "consumer", "balanced")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass(frozen=True)
class Character:
    """An agent's endowment-driven personality. NEUTRAL (the default) is an identity.

    - `risk_appetite` scales order size + aggressiveness (like a persistent risk_budget).
    - `soc_center` (when set) steers the battery target SOC toward the agent's natural
      operating point, clamped into [`soc_low`, `soc_high`] — a load-heavy consumer keeps
      charge in reserve (high centre), a producer leaves headroom to store surplus.
    """

    archetype: str = "balanced"
    risk_appetite: float = 1.0
    soc_center: float | None = None
    soc_low: float = 0.2
    soc_high: float = 0.9

    def is_neutral(self) -> bool:
        return self.risk_appetite == 1.0 and self.soc_center is None

    def apply(self, action: StrategyAction) -> StrategyAction:
        """Modulate a tactical action by this character. Identity when neutral."""
        if self.is_neutral():
            return action
        qty = _clamp(float(action.qty_fraction) * self.risk_appetite, 0.0, 1.0)
        aggr = _clamp(float(action.aggressiveness) * self.risk_appetite, 0.0, 1.0)
        soc = float(action.soc_target)
        if self.soc_center is not None:
            # Blend halfway toward the character's natural SOC, then keep it in band.
            soc = _clamp(0.5 * (soc + self.soc_center), self.soc_low, self.soc_high)
        return replace(action, qty_fraction=qty, aggressiveness=aggr, soc_target=soc)

    def to_public(self) -> dict:
        """Compact, JSON-safe view for the LLM message + diagnostics."""
        return {
            "archetype": self.archetype,
            "risk_appetite": round(float(self.risk_appetite), 2),
            "soc_center": None if self.soc_center is None else round(float(self.soc_center), 2),
            "soc_band": [round(float(self.soc_low), 2), round(float(self.soc_high), 2)],
        }


NEUTRAL_CHARACTER = Character()


def _endowment(params: object) -> tuple[float, float, float, float, float]:
    def g(name: str) -> float:
        try:
            return max(0.0, float(getattr(params, name, 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    battery = g("battery_kwh")
    generation = g("pv_kw_peak") + g("wind_kw_rated")
    load = g("load_kw_base")
    gas = g("gas_kw_max")
    return battery, generation, load, gas, g("pv_kw_peak")


def derive_character(params: object) -> Character:
    """Classify an agent's endowment into a behavioural archetype.

    Priority: dispatchable gas → battery-dominant arbitrageur → generation-dominant
    producer → load-dominant consumer → balanced (neutral identity). Thresholds are
    deliberately gentle so the modulation nudges behaviour rather than overriding the
    learned/scripted policy.
    """
    battery, generation, load, gas, _pv = _endowment(params)
    if gas > 0.0:
        # Dispatchable supply: keep the battery neutral, press modestly.
        return Character("dispatchable", risk_appetite=1.1, soc_center=0.5, soc_low=0.2, soc_high=0.9)
    if battery >= 2.5 * max(generation, load, 1.0):
        # Storage dominates: an arbitrageur that swings SOC wide and presses spreads.
        return Character("arbitrageur", risk_appetite=1.25, soc_center=0.5, soc_low=0.1, soc_high=0.95)
    if generation >= 1.5 * max(load, 0.5):
        # Generation dominates: a producer, sell-biased, leaves headroom to store surplus.
        return Character("producer", risk_appetite=1.1, soc_center=0.4, soc_low=0.15, soc_high=0.9)
    if load >= 1.5 * max(generation, 0.5):
        # Load dominates: a cost-minimising consumer — cautious, keeps charge in reserve.
        return Character("consumer", risk_appetite=0.85, soc_center=0.65, soc_low=0.3, soc_high=0.95)
    return NEUTRAL_CHARACTER


def endowment_summary(params: object) -> dict:
    """Compact endowment view for the LLM message (the agent's own assets)."""
    def g(name: str) -> float:
        try:
            return round(max(0.0, float(getattr(params, name, 0.0) or 0.0)), 3)
        except (TypeError, ValueError):
            return 0.0

    return {
        "battery_kwh": g("battery_kwh"),
        "pv_kw_peak": g("pv_kw_peak"),
        "wind_kw_rated": g("wind_kw_rated"),
        "load_kw_base": g("load_kw_base"),
        "gas_kw_max": g("gas_kw_max"),
    }
