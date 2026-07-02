// Arena helpers: cohort grouping + reflection lookup shared by the Arena page and
// the LLM/PPO influence panel.

import type { MarketAgent, MarketReflection } from "../api/types";

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
