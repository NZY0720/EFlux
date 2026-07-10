// Arena helpers: cohort grouping + reflection lookup shared by the Arena page and
// the LLM/PPO influence panel.

import type { ArenaPayload, MarketAgent, MarketReflection } from "../api/types";

export type ArenaRules = Omit<ArenaPayload, "agents">;

/**
 * Endowment signature for fair head-to-head grouping — agents with the same DER
 * nameplate compete on strategy alone. Rounded so float noise doesn't split cohorts.
 */
export function endowmentKey(a: MarketAgent): string {
  const r = (n: number) => Math.round(n * 10) / 10;
  return [
    `pv${r(a.pv_kw_peak)}`,
    `wd${r(a.wind_kw_rated)}`,
    `bk${r(a.battery_kwh)}`,
    `bp${r(a.battery_kw_max)}`,
    `ld${r(a.load_kw_base)}`,
  ].join(" ");
}

/** Newest reflection per agent name (entries arrive newest-first from the API). */
export function latestReflectionByAgent(entries: MarketReflection[]): Map<string, MarketReflection> {
  const out = new Map<string, MarketReflection>();
  for (const r of entries) {
    if (!out.has(r.vpp_name)) out.set(r.vpp_name, r);
  }
  return out;
}

/** Evidence must clear both thresholds before a contestant can be compared. */
export function hasArenaEvidence(agent: MarketAgent, rules: ArenaRules | null): boolean {
  return Boolean(
    rules &&
      agent.trade_count >= rules.min_trades &&
      agent.observation_min >= rules.min_observation_min,
  );
}

/** A comparison is honest only when both sides have a sufficient sample. */
export function comparisonReady(contenders: MarketAgent[], rules: ArenaRules | null): boolean {
  return contenders.length >= 2 && contenders.every((agent) => hasArenaEvidence(agent, rules));
}

export function evidenceProgress(agent: MarketAgent, rules: ArenaRules | null): string {
  if (!rules) return "Collecting market evidence";
  return `${Math.min(agent.trade_count, rules.min_trades)}/${rules.min_trades} trades · ${Math.min(
    Math.floor(agent.observation_min),
    rules.min_observation_min,
  )}/${rules.min_observation_min} min`;
}
