import { useEffect, useMemo, useState } from "react";

import { fetchMarketReflections } from "../api/client";
import type { MarketAgent, MarketReflection, PpoMetaControl } from "../api/types";
import { latestReflectionByAgent } from "../lib/arena";
import { formatCompactSigned } from "../lib/format";
import { EmptyState, StatusPill } from "./DashboardCard";

interface Props {
  agents: MarketAgent[];
}

const fmtSigned = (n: number, digits = 2) => `${n >= 0 ? "+" : ""}${n.toFixed(digits)}`;
const pct = (n: number | null | undefined) => (n === null || n === undefined ? "n/a" : `${(n * 100).toFixed(0)}%`);
const pctDash = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${(n * 100).toFixed(0)}%`);
const bps = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(0)} bps`);

function fallbackPct(agent: MarketAgent): number | null {
  if (agent.fallback_count === undefined && agent.veto_hold_count === undefined && agent.decide_ticks === undefined) return null;
  return ((agent.fallback_count ?? 0) + (agent.veto_hold_count ?? 0)) / Math.max(1, agent.decide_ticks ?? 0);
}

function metaChips(meta: PpoMetaControl | null | undefined): Array<[string, string]> {
  if (!meta) return [];
  const rows: Array<[string, number | undefined]> = [
    ["imb", meta.w_imbalance_mult],
    ["soc", meta.w_soc_mult],
    ["deg", meta.w_degrade_mult],
    ["lr", meta.lr],
    ["ent", meta.entropy_coef],
    ["kl", meta.kl_target],
    ["mode", meta.mode_reg_coef],
  ];
  return rows
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => [k, k === "lr" ? Number(v).toExponential(1) : Number(v).toFixed(k === "mode" ? 2 : 3)]);
}

/**
 * Real-price A/B view: every LLM hybrid has a strategist-less PPO mirror with the
 * same seed and DER params. Deltas isolate the LLM guidance/meta-control layer.
 */
export default function LlmPpoInfluencePanel({ agents }: Props) {
  const [reflections, setReflections] = useState<MarketReflection[]>([]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchMarketReflections(80);
        if (!cancelled) setReflections(data);
      } catch {
        /* keep the last successful reflection snapshot */
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const pairs = useMemo(() => {
    const byName = new Map(agents.map((a) => [a.name, a]));
    const latest = latestReflectionByAgent(reflections);
    return agents
      .filter((a) => a.mirror_of)
      .map((mirror) => {
        const llm = byName.get(mirror.mirror_of ?? "");
        if (!llm) return null;
        return { llm, mirror, reflection: latest.get(llm.name) ?? null };
      })
      .filter((x): x is { llm: MarketAgent; mirror: MarketAgent; reflection: MarketReflection | null } => x !== null)
      .sort((a, b) => Number(b.llm.pnl) - Number(a.llm.pnl));
  }, [agents, reflections]);

  if (pairs.length === 0) {
    return <EmptyState className="h-72" title="Waiting for LLM/PPO mirror pairs..." />;
  }

  return (
    <div className="h-72 space-y-2 overflow-auto pr-1">
      {pairs.map(({ llm, mirror, reflection }) => {
        const pnlDelta = Number(llm.pnl) - Number(mirror.pnl);
        const socDelta = llm.soc_frac - mirror.soc_frac;
        const tradeDelta = llm.trade_count - mirror.trade_count;
        const fallbackRate = fallbackPct(llm);
        const chips = metaChips(reflection?.meta_control);
        return (
          <div key={mirror.id} className="eflux-inset rounded-lg p-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-xs font-medium text-[var(--text)]">{llm.name}</div>
                <div className="text-[11px] text-[var(--text-subtle)]">vs {mirror.name}</div>
              </div>
              <div className={`text-sm font-semibold tabular-nums ${pnlDelta >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>
                {fmtSigned(pnlDelta)}
              </div>
            </div>

            <div className="mt-2 grid grid-cols-3 gap-1 text-[11px]">
              <Metric label="PnL delta" value={`$${fmtSigned(pnlDelta)}`} good={pnlDelta >= 0} />
              <Metric label="SOC delta" value={fmtSigned(socDelta * 100, 0) + "%"} good={Math.abs(socDelta) <= 0.1} />
              <Metric label="Trade delta" value={formatCompactSigned(tradeDelta)} good={tradeDelta >= 0} />
            </div>

            <div className="mt-1 grid grid-cols-3 gap-1 text-[11px]">
              <Metric label="Fallback %" value={pctDash(fallbackRate)} good={fallbackRate === null || fallbackRate <= 0.05} />
              <Metric label="Guidance Δ" value={pctDash(llm.guidance_change_rate)} good={(llm.guidance_change_rate ?? 0) <= 0.5} />
              <Metric label="Price dev" value={bps(llm.avg_price_dev_bps)} good={Math.abs(llm.avg_price_dev_bps ?? 0) <= 100} />
            </div>

            <div className="mt-2 flex flex-wrap gap-1 text-[11px]">
              {reflection?.mode_pin && (
                <StatusPill tone="accent" className="py-0 text-[11px]">pinned: {reflection.mode_pin}</StatusPill>
              )}
              {reflection?.preferred_modes?.slice(0, 3).map((m) => (
                <StatusPill key={`p-${m}`} tone="success" className="py-0 text-[11px]">prefer {m}</StatusPill>
              ))}
              {reflection?.avoid_modes?.slice(0, 2).map((m) => (
                <StatusPill key={`a-${m}`} tone="danger" className="py-0 text-[11px]">avoid {m}</StatusPill>
              ))}
              {reflection && (
                <>
                  <StatusPill tone="accent" className="py-0 text-[11px]">risk {pct(reflection.risk_budget)}</StatusPill>
                  <StatusPill tone="amber" className="py-0 text-[11px]">SOC {pct(reflection.soc_target)}</StatusPill>
                </>
              )}
              {chips.map(([k, v]) => (
                <StatusPill key={`${k}-${v}`} tone="violet" className="py-0 text-[11px]">{k} {v}</StatusPill>
              ))}
              {!reflection && <span className="text-[var(--text-subtle)]">No LLM guidance yet</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Metric({ label, value, good }: { label: string; value: string; good: boolean }) {
  return (
    <div className="rounded-md border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-1">
      <div className="text-[10px] uppercase tracking-wide text-[var(--text-subtle)]">{label}</div>
      <div className={`mt-0.5 font-semibold tabular-nums ${good ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>{value}</div>
    </div>
  );
}
