import { Trophy } from "lucide-react";

import type { MarketAgent } from "../api/types";
import { formatCompactCount } from "../lib/format";
import { EmptyState, StatusPill, TableShell } from "./DashboardCard";

interface Props {
  agents: MarketAgent[];
}

const fmtUsd = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;

function typeBadge(a: MarketAgent): { label: string; tone: "violet" | "accent" | "success" | "amber" | "muted" } {
  // a.strategy is a descriptive string, e.g. "TruthfulAgent",
  // "StrategyAgent (PPO mirror)", "HybridPolicyAgent (opencode:deepseek-v4-pro)".
  const s = a.strategy ?? "";
  if (a.is_llm) return { label: "LLM", tone: "violet" };
  if (s.includes("PPO mirror")) return { label: "PPO mirror", tone: "success" };
  if (s.includes("Strategy") || s.includes("PPO")) return { label: "PPO", tone: "accent" };
  if (s.includes("Truthful")) return { label: "truthful", tone: "muted" };
  if (s.includes("Gas")) return { label: "gas", tone: "amber" };
  if (s.includes("ZIP")) return { label: "ZIP", tone: "muted" };
  if (s.includes("AA")) return { label: "AA", tone: "muted" };
  if (s.includes("GD")) return { label: "GD", tone: "muted" };
  return { label: s || "agent", tone: "muted" };
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
        <span className="text-[var(--text-muted)]">
          Fleet PnL{" "}
          <span className={fleetPnl >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}>{fmtUsd(fleetPnl)}</span>{" "}
          <span className="text-[var(--text-subtle)]">($)</span>
        </span>
        {top && (
          <span className="flex items-center gap-1.5 text-[var(--text-muted)]">
            <Trophy size={15} className="text-[var(--warning)]" />
            Leader <span className="text-[var(--text)]">{top.name}</span>{" "}
            <span className={Number(top.pnl) >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}>
              {fmtUsd(Number(top.pnl))}
            </span>
          </span>
        )}
      </div>
      <TableShell className="h-72">
        <table className="eflux-table text-xs">
          <thead className="sticky top-0 z-10">
            <tr>
              <th className="px-3 py-2 text-left font-semibold">#</th>
              <th className="px-3 py-2 text-left font-semibold">Agent</th>
              <th className="px-3 py-2 text-left font-semibold">Type</th>
              <th className="px-3 py-2 text-right font-semibold">PnL ($)</th>
              <th className="px-3 py-2 text-right font-semibold">Net kW</th>
              <th className="px-3 py-2 text-right font-semibold">SOC</th>
              <th className="px-3 py-2 text-right font-semibold">Trades</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((a, i) => {
              const badge = typeBadge(a);
              const pnl = Number(a.pnl);
              return (
                <tr key={a.id}>
                  <td className="px-3 py-1.5 text-[var(--text-subtle)] tabular-nums">{i + 1}</td>
                  <td className="px-3 py-1.5 text-[var(--text)]">{a.name}</td>
                  <td className="px-3 py-1.5">
                    <StatusPill tone={badge.tone} className="py-0 text-[10px]">{badge.label}</StatusPill>
                  </td>
                  <td className={`px-3 py-1.5 text-right tabular-nums ${pnl >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>
                    {fmtUsd(pnl)}
                  </td>
                  <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">{a.net_kw.toFixed(2)}</td>
                  <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">{(a.soc_frac * 100).toFixed(0)}%</td>
                  <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums" title={`${a.trade_count} trades`}>
                    {formatCompactCount(a.trade_count)}
                  </td>
                </tr>
              );
            })}
            {ranked.length === 0 && (
              <tr>
                <td colSpan={7} className="p-3">
                  <EmptyState icon={Trophy} title="Waiting for agents..." />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </TableShell>
    </div>
  );
}
