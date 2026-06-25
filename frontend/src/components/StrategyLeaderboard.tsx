import type { MarketAgent } from "../api/types";
import { formatCompactCount } from "../lib/format";

interface Props {
  agents: MarketAgent[];
}

const fmtUsd = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;

function typeBadge(a: MarketAgent): { label: string; cls: string } {
  // a.strategy is a descriptive string, e.g. "TruthfulAgent",
  // "StrategyAgent (PPO mirror)", "HybridPolicyAgent (xiaomi-mimo:...)".
  const s = a.strategy ?? "";
  if (a.is_llm) return { label: "LLM", cls: "border-violet-800 bg-violet-950/40 text-violet-300" };
  if (s.includes("PPO mirror")) return { label: "PPO mirror", cls: "border-teal-800 bg-teal-950/40 text-teal-300" };
  if (s.includes("Strategy") || s.includes("PPO")) return { label: "PPO", cls: "border-sky-800 bg-sky-950/40 text-sky-300" };
  if (s.includes("Truthful")) return { label: "truthful", cls: "border-slate-700 bg-slate-900 text-slate-300" };
  if (s.includes("Gas")) return { label: "gas", cls: "border-amber-800 bg-amber-950/40 text-amber-300" };
  if (s.includes("ZI")) return { label: "ZI", cls: "border-slate-700 bg-slate-900 text-slate-400" };
  return { label: s || "agent", cls: "border-slate-700 bg-slate-900 text-slate-300" };
}

/**
 * Real-Time market centerpiece: agents ranked by PnL earned trading against the
 * live grid price. The header summarizes fleet PnL and the current leader.
 */
export default function StrategyLeaderboard({ agents }: Props) {
  const ranked = [...agents].sort((a, b) => Number(b.pnl) - Number(a.pnl));
  const fleetPnl = agents.reduce((s, a) => s + (Number(a.pnl) || 0), 0);
  const top = ranked[0];

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-slate-400">
          Fleet PnL{" "}
          <span className={fleetPnl >= 0 ? "text-emerald-300" : "text-rose-300"}>{fmtUsd(fleetPnl)}</span>{" "}
          <span className="text-slate-600">($)</span>
        </span>
        {top && (
          <span className="text-slate-400">
            Leader <span className="text-white">{top.name}</span>{" "}
            <span className={Number(top.pnl) >= 0 ? "text-emerald-300" : "text-rose-300"}>
              {fmtUsd(Number(top.pnl))}
            </span>
          </span>
        )}
      </div>
      <div className="h-72 overflow-auto rounded border border-slate-800 bg-slate-900/60">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-900 text-slate-400">
            <tr>
              <th className="px-3 py-2 text-left">#</th>
              <th className="px-3 py-2 text-left">Agent</th>
              <th className="px-3 py-2 text-left">Type</th>
              <th className="px-3 py-2 text-right">PnL ($)</th>
              <th className="px-3 py-2 text-right">Net kW</th>
              <th className="px-3 py-2 text-right">SOC</th>
              <th className="px-3 py-2 text-right">Trades</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((a, i) => {
              const badge = typeBadge(a);
              const pnl = Number(a.pnl);
              return (
                <tr key={a.id} className="border-t border-slate-800 hover:bg-slate-800/50">
                  <td className="px-3 py-1.5 text-slate-500 tabular-nums">{i + 1}</td>
                  <td className="px-3 py-1.5 text-slate-200">{a.name}</td>
                  <td className="px-3 py-1.5">
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${badge.cls}`}>{badge.label}</span>
                  </td>
                  <td className={`px-3 py-1.5 text-right tabular-nums ${pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {fmtUsd(pnl)}
                  </td>
                  <td className="px-3 py-1.5 text-right text-slate-300 tabular-nums">{a.net_kw.toFixed(2)}</td>
                  <td className="px-3 py-1.5 text-right text-slate-300 tabular-nums">{(a.soc_frac * 100).toFixed(0)}%</td>
                  <td className="px-3 py-1.5 text-right text-slate-400 tabular-nums" title={`${a.trade_count} trades`}>
                    {formatCompactCount(a.trade_count)}
                  </td>
                </tr>
              );
            })}
            {ranked.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-4 text-center text-slate-500">
                  Waiting for agents…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
