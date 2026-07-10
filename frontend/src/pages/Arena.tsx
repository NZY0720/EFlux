import { useEffect, useMemo, useState } from "react";
import { BrainCircuit, ChartSpline, MessagesSquare, Swords } from "lucide-react";

import { fetchMarketReflections } from "../api/client";
import type { MarketAgent, MarketReflection } from "../api/types";
import Chatroom from "../components/Chatroom";
import { CardTitle, DashboardCard, EmptyState, StatusPill } from "../components/DashboardCard";
import EquityCurves from "../components/EquityCurves";
import LlmPpoInfluencePanel from "../components/LlmPpoInfluencePanel";
import { comparisonReady, endowmentKey, evidenceProgress, hasArenaEvidence, latestReflectionByAgent } from "../lib/arena";
import { useServerEquity } from "../state/useServerEquity";
import { useStrategyPnl } from "../state/useStrategyPnl";

const fmtUsd = (s: string) => {
  const n = Number(s);
  if (Math.abs(n) < 0.005) return "$0";
  return `${n >= 0 ? "+" : "-"}$${Math.abs(n).toFixed(2)}`;
};

const fmtDelta = (n: number) => (Math.abs(n) < 0.005 ? "even" : `${n >= 0 ? "+" : "-"}$${Math.abs(n).toFixed(2)}`);

const median = (xs: number[]): number => {
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
};

/**
 * Model arena. Endowment cohorts isolate strategy, and every comparison is held
 * until each contender clears the server-provided evidence threshold.
 */
export default function Arena() {
  const { agents, arena } = useStrategyPnl(2000);
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
    for (const agent of llmAgents) {
      const key = endowmentKey(agent);
      groups.set(key, [...(groups.get(key) ?? []), agent]);
    }
    return [...groups.entries()].filter(([, members]) => members.length >= 2);
  }, [llmAgents]);

  const contenderPool = useMemo(() => {
    const pool = cohort === null ? llmAgents : (cohorts.find(([key]) => key === cohort)?.[1] ?? []);
    return [...pool];
  }, [llmAgents, cohorts, cohort]);
  const isComparisonReady = useMemo(() => comparisonReady(contenderPool, arena), [contenderPool, arena]);
  const contenders = useMemo(
    () =>
      [...contenderPool].sort((a, b) =>
        isComparisonReady ? Number(b.pnl) - Number(a.pnl) : a.name.localeCompare(b.name),
      ),
    [contenderPool, isComparisonReady],
  );
  const latest = useMemo(() => latestReflectionByAgent(reflections), [reflections]);
  const medianPnl = useMemo(
    () => (isComparisonReady ? median(contenders.map((agent) => Number(agent.pnl))) : null),
    [contenders, isComparisonReady],
  );
  const equity = useServerEquity(contenders.slice(0, 8).map((agent) => `name:${agent.name}`));
  const hasMirrors = agents.some((agent) => agent.mirror_of !== null);

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold text-[var(--text)]">
          <Swords size={22} className="text-[var(--violet)]" />
          Model arena
        </h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          LLM-steered agents share a market. Comparisons appear only after each contender has
          enough trades and simulated observation time.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-1.5" aria-label="Arena endowment cohorts">
        <button type="button" onClick={() => setCohort(null)} className={`eflux-chip ${cohort === null ? "eflux-chip-active" : ""}`}>
          All LLM agents ({llmAgents.length})
        </button>
        {cohorts.map(([key, members]) => (
          <button
            key={key}
            type="button"
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
          {contenders.map((agent) => {
            const reflection = latest.get(agent.name);
            const pnl = Number(agent.pnl);
            const delta = medianPnl === null ? null : pnl - medianPnl;
            const evidenceReady = hasArenaEvidence(agent, arena);
            return (
              <DashboardCard key={agent.id} className={isComparisonReady && delta !== null && delta > 0 ? "ring-1 ring-[var(--violet)]" : ""}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-medium text-[var(--text)]">{agent.name}</div>
                    <StatusPill tone="violet" className="mt-1 py-0 text-[10px]">
                      <BrainCircuit size={11} className="mr-0.5 inline" />
                      {agent.llm_model ?? "scripted baseline"}
                    </StatusPill>
                  </div>
                  {isComparisonReady && delta !== null ? (
                    <div className="text-right">
                      <div className={`text-lg font-semibold tabular-nums ${pnl >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>
                        {fmtUsd(agent.pnl)}
                      </div>
                      <div className="text-[10px] text-[var(--text-subtle)]">{fmtDelta(delta)} vs cohort median</div>
                    </div>
                  ) : (
                    <div className="max-w-[11rem] text-right text-[10px] text-[var(--text-muted)]">
                      <div className="font-medium text-[var(--text)]">Collecting evidence</div>
                      <div className="mt-0.5 tabular-nums">{evidenceProgress(agent, arena)}</div>
                    </div>
                  )}
                </div>
                <div className="mt-2 grid grid-cols-3 gap-1 text-center text-[11px]">
                  <div className="eflux-inset rounded-md px-1 py-1">
                    <div className="text-[var(--text-subtle)]">SOC</div>
                    <div className="font-semibold text-[var(--text)]">{(agent.soc_frac * 100).toFixed(0)}%</div>
                  </div>
                  <div className="eflux-inset rounded-md px-1 py-1">
                    <div className="text-[var(--text-subtle)]">Trades</div>
                    <div className="font-semibold text-[var(--text)] tabular-nums">
                      {arena ? `${Math.min(agent.trade_count, arena.min_trades)}/${arena.min_trades}` : "..."}
                    </div>
                  </div>
                  <div className="eflux-inset rounded-md px-1 py-1">
                    <div className="text-[var(--text-subtle)]">Observed</div>
                    <div className="font-semibold text-[var(--text)] tabular-nums">
                      {arena ? `${Math.min(Math.floor(agent.observation_min), arena.min_observation_min)}/${arena.min_observation_min} min` : "..."}
                    </div>
                  </div>
                </div>
                <div className="mt-2 min-h-[54px] text-xs">
                  {reflection ? (
                    <>
                      <div className="flex flex-wrap gap-1">
                        {reflection.preferred_modes?.slice(0, 2).map((mode) => (
                          <StatusPill key={mode} tone="success" className="py-0 text-[10px]">{mode}</StatusPill>
                        ))}
                        {reflection.risk_budget != null && (
                          <StatusPill tone="accent" className="py-0 text-[10px]">risk {(reflection.risk_budget * 100).toFixed(0)}%</StatusPill>
                        )}
                      </div>
                      <p className="mt-1 line-clamp-2 text-[var(--text-muted)]" title={reflection.rationale}>
                        “{reflection.rationale}”
                      </p>
                    </>
                  ) : (
                    <span className="text-[var(--text-subtle)]">No guidance recorded yet</span>
                  )}
                  {!evidenceReady && <span className="sr-only">This contender is not yet eligible for comparison.</span>}
                </div>
              </DashboardCard>
            );
          })}
        </div>
      )}

      <DashboardCard>
        <CardTitle icon={ChartSpline}>Equity comparison</CardTitle>
        {isComparisonReady ? (
          <EquityCurves history={equity} topN={8} />
        ) : (
          <p className="text-sm text-[var(--text-muted)]">
            Collecting evidence before plotting a win-loss comparison. Each contender needs the
            configured trade and observation minimums.
          </p>
        )}
      </DashboardCard>

      {hasMirrors && isComparisonReady && (
        <DashboardCard>
          <CardTitle icon={Swords}>LLM vs PPO mirror, does the LLM layer help?</CardTitle>
          <p className="mb-2 text-xs text-[var(--text-muted)]">
            Each LLM hybrid has a strategist-less PPO twin with identical seed and assets. The
            delta isolates the LLM guidance layer.
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
