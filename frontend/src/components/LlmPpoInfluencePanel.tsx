import { useEffect, useMemo, useState } from "react";

import { fetchMarketReflections } from "../api/client";
import type { MarketAgent, MarketReflection, PpoMetaControl } from "../api/types";
import { formatCompactSigned } from "../lib/format";

interface Props {
  agents: MarketAgent[];
}

const fmtSigned = (n: number, digits = 2) => `${n >= 0 ? "+" : ""}${n.toFixed(digits)}`;
const pct = (n: number | null | undefined) => (n === null || n === undefined ? "n/a" : `${(n * 100).toFixed(0)}%`);

function latestReflectionByAgent(entries: MarketReflection[]): Map<string, MarketReflection> {
  const out = new Map<string, MarketReflection>();
  for (const r of entries) {
    if (!out.has(r.vpp_name)) out.set(r.vpp_name, r);
  }
  return out;
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
    return (
      <div className="flex h-72 items-center justify-center text-sm text-slate-500">
        Waiting for LLM/PPO mirror pairs...
      </div>
    );
  }

  return (
    <div className="h-72 space-y-2 overflow-auto pr-1">
      {pairs.map(({ llm, mirror, reflection }) => {
        const pnlDelta = Number(llm.pnl) - Number(mirror.pnl);
        const socDelta = llm.soc_frac - mirror.soc_frac;
        const tradeDelta = llm.trade_count - mirror.trade_count;
        const chips = metaChips(reflection?.meta_control);
        return (
          <div key={mirror.id} className="rounded border border-slate-800 bg-slate-950/40 p-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-xs font-medium text-slate-200">{llm.name}</div>
                <div className="text-[11px] text-slate-500">vs {mirror.name}</div>
              </div>
              <div className={`text-sm font-semibold tabular-nums ${pnlDelta >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                {fmtSigned(pnlDelta)}
              </div>
            </div>

            <div className="mt-2 grid grid-cols-3 gap-1 text-[11px]">
              <Metric label="PnL delta" value={`$${fmtSigned(pnlDelta)}`} good={pnlDelta >= 0} />
              <Metric label="SOC delta" value={fmtSigned(socDelta * 100, 0) + "%"} good={Math.abs(socDelta) <= 0.1} />
              <Metric label="Trade delta" value={formatCompactSigned(tradeDelta)} good={tradeDelta >= 0} />
            </div>

            <div className="mt-2 flex flex-wrap gap-1 text-[11px]">
              {reflection?.preferred_modes?.slice(0, 3).map((m) => (
                <span key={`p-${m}`} className="rounded border border-emerald-800 bg-emerald-950/40 px-1.5 py-0.5 text-emerald-300">
                  prefer {m}
                </span>
              ))}
              {reflection?.avoid_modes?.slice(0, 2).map((m) => (
                <span key={`a-${m}`} className="rounded border border-rose-800 bg-rose-950/40 px-1.5 py-0.5 text-rose-300">
                  avoid {m}
                </span>
              ))}
              {reflection && (
                <>
                  <span className="rounded border border-sky-800 bg-sky-950/40 px-1.5 py-0.5 text-sky-300">
                    risk {pct(reflection.risk_budget)}
                  </span>
                  <span className="rounded border border-amber-800 bg-amber-950/40 px-1.5 py-0.5 text-amber-300">
                    SOC {pct(reflection.soc_target)}
                  </span>
                </>
              )}
              {chips.map(([k, v]) => (
                <span key={`${k}-${v}`} className="rounded border border-violet-800 bg-violet-950/40 px-1.5 py-0.5 text-violet-300">
                  {k} {v}
                </span>
              ))}
              {!reflection && <span className="text-slate-500">No LLM guidance yet</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Metric({ label, value, good }: { label: string; value: string; good: boolean }) {
  return (
    <div className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-0.5 font-semibold tabular-nums ${good ? "text-emerald-300" : "text-rose-300"}`}>{value}</div>
    </div>
  );
}
