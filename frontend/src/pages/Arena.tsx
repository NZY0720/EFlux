import { useEffect, useMemo, useState } from "react";
import { BrainCircuit, ChartSpline, MessagesSquare, Swords } from "lucide-react";

import { fetchMarketReflections } from "../api/client";
import type { MarketAgent, MarketReflection } from "../api/types";
import Chatroom from "../components/Chatroom";
import { CardTitle, DashboardCard, EmptyState, StatusPill } from "../components/DashboardCard";
import EquityCurves from "../components/EquityCurves";
import LlmPpoInfluencePanel from "../components/LlmPpoInfluencePanel";
import { endowmentKey, latestReflectionByAgent } from "../lib/arena";
import { useServerEquity } from "../state/useServerEquity";
import { useStrategyPnl } from "../state/useStrategyPnl";

const fmtUsd = (s: string) => {
  const n = Number(s);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;
};

const median = (xs: number[]): number => {
  if (xs.length === 0) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
};

/**
 * Model arena — LLM-steered agents head-to-head. Cohorts group agents with the same
 * DER endowment so the comparison isolates strategy (the model), not asset size;
 * the "all" view relies on that same intuition loosely (PnL is per-endowment here,
 * the Leaderboard's score v1 is the rigorous cross-endowment metric).
 */
export default function Arena() {
  const { agents } = useStrategyPnl(2000);
  const [reflections, setReflections] = useState<MarketReflection[]>([]);
  const [cohort, setCohort] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchMarketReflections(80);
        if (!cancelled) setReflections(data);
      } catch {
        /* keep last */
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const llmAgents = useMemo(() => agents.filter((a) => a.is_llm), [agents]);

  const cohorts = useMemo(() => {
    const groups = new Map<string, MarketAgent[]>();
    for (const a of llmAgents) {
      const key = endowmentKey(a);
      groups.set(key, [...(groups.get(key) ?? []), a]);
    }
    // Only endowment groups with a real head-to-head (2+ members) get a chip.
    return [...groups.entries()].filter(([, members]) => members.length >= 2);
  }, [llmAgents]);

  const contenders = useMemo(() => {
    const pool = cohort === null ? llmAgents : (cohorts.find(([k]) => k === cohort)?.[1] ?? []);
    return [...pool].sort((a, b) => Number(b.pnl) - Number(a.pnl));
  }, [llmAgents, cohorts, cohort]);

  const latest = useMemo(() => latestReflectionByAgent(reflections), [reflections]);
  const medianPnl = useMemo(() => median(contenders.map((a) => Number(a.pnl))), [contenders]);

  // Server-side equity history (survives refresh/restart) for the contenders.
  const equity = useServerEquity(contenders.slice(0, 8).map((a) => `name:${a.name}`));

  const hasMirrors = agents.some((a) => a.mirror_of !== null);

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold text-[var(--text)]">
          <Swords size={22} className="text-[var(--violet)]" />
          Model arena
        </h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          LLM-steered agents head-to-head: same market, different models. Pick an endowment
          cohort to compare strategy on identical assets; every claim below is live.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <button onClick={() => setCohort(null)} className={`eflux-chip ${cohort === null ? "eflux-chip-active" : ""}`}>
          All LLM agents ({llmAgents.length})
        </button>
        {cohorts.map(([key, members]) => (
          <button
            key={key}
            onClick={() => setCohort(cohort === key ? null : key)}
            className={`eflux-chip ${cohort === key ? "eflux-chip-active" : ""}`}
            title={key}
          >
            same endowment ×{members.length}
          </button>
        ))}
      </div>

      {contenders.length === 0 ? (
        <EmptyState
          icon={Swords}
          title="No LLM agents in this market"
          body="Deploy managed agents (My VPPs) or run a roster with hybrid entries to populate the arena."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {contenders.map((a, i) => {
            const r = latest.get(a.name);
            const pnl = Number(a.pnl);
            const delta = pnl - medianPnl;
            return (
              <DashboardCard key={a.id} className={i === 0 ? "ring-1 ring-[var(--violet)]" : ""}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-medium text-[var(--text)]">{a.name}</div>
                    <StatusPill tone="violet" className="mt-1 py-0 text-[10px]">
                      <BrainCircuit size={11} className="mr-0.5 inline" />
                      {a.llm_model ?? "scripted baseline"}
                    </StatusPill>
                  </div>
                  <div className="text-right">
                    <div className={`text-lg font-semibold tabular-nums ${pnl >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>
                      {fmtUsd(a.pnl)}
                    </div>
                    <div className="text-[10px] text-[var(--text-subtle)]">
                      {delta >= 0 ? "+" : ""}
                      {delta.toFixed(2)} vs cohort median
                    </div>
                  </div>
                </div>
                <div className="mt-2 grid grid-cols-3 gap-1 text-center text-[11px]">
                  <div className="eflux-inset rounded-md px-1 py-1">
                    <div className="text-[var(--text-subtle)]">SOC</div>
                    <div className="font-semibold text-[var(--text)]">{(a.soc_frac * 100).toFixed(0)}%</div>
                  </div>
                  <div className="eflux-inset rounded-md px-1 py-1">
                    <div className="text-[var(--text-subtle)]">Trades</div>
                    <div className="font-semibold text-[var(--text)]">{a.trade_count}</div>
                  </div>
                  <div className="eflux-inset rounded-md px-1 py-1">
                    <div className="text-[var(--text-subtle)]">Net kW</div>
                    <div className="font-semibold text-[var(--text)]">{a.net_kw.toFixed(1)}</div>
                  </div>
                </div>
                <div className="mt-2 min-h-[54px] text-xs">
                  {r ? (
                    <>
                      <div className="flex flex-wrap gap-1">
                        {r.preferred_modes?.slice(0, 2).map((m) => (
                          <StatusPill key={m} tone="success" className="py-0 text-[10px]">{m}</StatusPill>
                        ))}
                        {r.risk_budget != null && (
                          <StatusPill tone="accent" className="py-0 text-[10px]">risk {(r.risk_budget * 100).toFixed(0)}%</StatusPill>
                        )}
                      </div>
                      <p className="mt-1 line-clamp-2 text-[var(--text-muted)]" title={r.rationale}>
                        “{r.rationale}”
                      </p>
                    </>
                  ) : (
                    <span className="text-[var(--text-subtle)]">No guidance recorded yet</span>
                  )}
                </div>
              </DashboardCard>
            );
          })}
        </div>
      )}

      <DashboardCard>
        <CardTitle icon={ChartSpline}>Equity — server history (top {Math.min(contenders.length, 8)} contenders)</CardTitle>
        <EquityCurves history={equity} topN={8} />
      </DashboardCard>

      {hasMirrors && (
        <DashboardCard>
          <CardTitle icon={Swords}>LLM vs PPO mirror — does the LLM layer help?</CardTitle>
          <p className="mb-2 text-xs text-[var(--text-muted)]">
            Each LLM hybrid has a strategist-less PPO twin with identical seed and assets;
            the delta isolates exactly the LLM guidance layer.
          </p>
          <LlmPpoInfluencePanel agents={agents} />
        </DashboardCard>
      )}

      <DashboardCard>
        <CardTitle icon={MessagesSquare}>Agent chatroom</CardTitle>
        <Chatroom />
      </DashboardCard>
    </div>
  );
}
