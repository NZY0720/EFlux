import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, FlaskConical, Images, ListChecks } from "lucide-react";

import { benchmarkChartUrl, fetchBenchmarkDetail } from "../api/client";
import type { BenchmarkDetail, BenchmarkParticipant } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill, TableShell } from "../components/DashboardCard";
import { EvaluationNav } from "../components/WorkspaceNav";

const fmtWindow = (s: string | null, e: string | null) =>
  s && e ? `${s.slice(0, 10)} → ${e.slice(0, 10)}` : "—";

type SortKey = keyof Pick<
  BenchmarkParticipant,
  "realized_pnl" | "mark_to_market" | "trade_count" | "energy_sold_kwh" | "risk_rejections"
>;

function RunDetail({ runId }: { runId: string }) {
  const [detail, setDetail] = useState<BenchmarkDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("mark_to_market");

  useEffect(() => {
    fetchBenchmarkDetail(runId)
      .then(setDetail)
      .catch((e) => setError((e as Error).message));
  }, [runId]);

  const participants = useMemo(
    () => [...(detail?.participants ?? [])].sort((a, b) => Number(b[sortKey]) - Number(a[sortKey])),
    [detail, sortKey],
  );

  const manifestFacts: Array<[string, unknown]> = detail
    ? [
        ["Market mode", detail.manifest.market_mode],
        ["Status", detail.manifest.status ?? "incomplete"],
        ["Window", fmtWindow(detail.manifest.start as string | null, detail.manifest.end as string | null)],
        ["Tick (s)", detail.manifest.tick_seconds],
        ["Ticks run", detail.manifest.ticks_run],
        ["LLM mode", detail.manifest.llm_mode],
        ["LLM calls", `${detail.manifest.llm_calls ?? "—"} / ${detail.manifest.expected_llm_calls ?? "—"} expected`],
        ["Price source", detail.manifest.price_source],
        ["Participants", detail.manifest.live_participants],
        ["PPO retrained", detail.manifest.train_ppo ? "yes" : "no"],
      ]
    : [];

  const th = (label: string, key?: SortKey) => (
    <th
      className={`px-3 py-2 text-right font-semibold ${key ? "cursor-pointer select-none hover:text-[var(--text)]" : ""}`}
      onClick={key ? () => setSortKey(key) : undefined}
    >
      {label}
      {key && sortKey === key ? " ▾" : ""}
    </th>
  );

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
      <EvaluationNav />
      <div className="flex items-center gap-3">
        <Link to="/evaluate/runs" className="eflux-btn h-9 px-3">
          <ArrowLeft size={15} />
          All runs
        </Link>
        <h2 className="font-mono text-xl font-semibold text-[var(--text)]">{runId}</h2>
      </div>

      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}

      {detail && (
        <>
          <DashboardCard>
            <CardTitle icon={FlaskConical}>Run manifest</CardTitle>
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3 lg:grid-cols-5">
              {manifestFacts.map(([k, v]) => (
                <div key={k}>
                  <div className="text-[11px] uppercase tracking-wide text-[var(--text-subtle)]">{k}</div>
                  <div className="text-[var(--text)]">{String(v ?? "—")}</div>
                </div>
              ))}
            </div>
          </DashboardCard>

          <DashboardCard>
            <CardTitle icon={ListChecks}>Participants ({participants.length})</CardTitle>
            <TableShell className="max-h-[480px]">
              <table className="eflux-table text-xs">
                <thead className="sticky top-0 z-10">
                  <tr>
                    <th className="px-3 py-2 text-left font-semibold">Agent</th>
                    <th className="px-3 py-2 text-left font-semibold">Strategy</th>
                    {th("Realized PnL", "realized_pnl")}
                    {th("Mark-to-market", "mark_to_market")}
                    {th("Trades", "trade_count")}
                    {th("Sold kWh", "energy_sold_kwh")}
                    {th("Risk rej.", "risk_rejections")}
                    {th("Final SOC")}
                  </tr>
                </thead>
                <tbody>
                  {participants.map((p) => (
                    <tr key={p.name}>
                      <td className="px-3 py-1.5 text-[var(--text)]">
                        {p.name}
                        {p.is_llm && <StatusPill tone="violet" className="ml-1.5 py-0 text-[9px]">LLM</StatusPill>}
                      </td>
                      <td className="px-3 py-1.5 text-[var(--text-muted)]">{p.strategy}</td>
                      <td className={`px-3 py-1.5 text-right tabular-nums ${p.realized_pnl >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>
                        {p.realized_pnl.toFixed(2)}
                      </td>
                      <td className={`px-3 py-1.5 text-right tabular-nums ${p.mark_to_market >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>
                        {p.mark_to_market.toFixed(2)}
                      </td>
                      <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">{p.trade_count}</td>
                      <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">{p.energy_sold_kwh.toFixed(1)}</td>
                      <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">{p.risk_rejections}</td>
                      <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">{(p.final_soc_frac * 100).toFixed(0)}%</td>
                    </tr>
                  ))}
                  {participants.length === 0 && (
                    <tr>
                      <td colSpan={8} className="p-3">
                        <EmptyState title="No participant metrics in this run" />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </TableShell>
          </DashboardCard>

          {detail.charts.length > 0 && (
            <DashboardCard>
              <CardTitle icon={Images}>Charts</CardTitle>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {detail.charts.map((c) => (
                  <figure key={c}>
                    <img
                      src={benchmarkChartUrl(runId, c)}
                      alt={c}
                      loading="lazy"
                      className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-elevated)]"
                    />
                    <figcaption className="mt-1 text-center font-mono text-[10px] text-[var(--text-subtle)]">{c}</figcaption>
                  </figure>
                ))}
              </div>
            </DashboardCard>
          )}
        </>
      )}
    </div>
  );
}

export default function Benchmarks() {
  const { runId = "" } = useParams<{ runId: string }>();
  return <RunDetail runId={runId} />;
}
