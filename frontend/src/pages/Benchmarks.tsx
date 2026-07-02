import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  BadgeCheck,
  CloudUpload,
  FlaskConical,
  Images,
  ListChecks,
  Terminal,
} from "lucide-react";

import { benchmarkChartUrl, fetchBenchmarkDetail, fetchBenchmarks } from "../api/client";
import type { BenchmarkDetail, BenchmarkParticipant, BenchmarkSummary } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill, TableShell } from "../components/DashboardCard";

const fmtWindow = (s: string | null, e: string | null) =>
  s && e ? `${s.slice(0, 10)} → ${e.slice(0, 10)}` : "—";

function StatusBadge({ status }: { status: string }) {
  const tone = status === "ok" ? "success" : status === "failed" ? "danger" : "amber";
  return <StatusPill tone={tone} className="py-0 text-[10px]">{status}</StatusPill>;
}

function LlmIntegrityChip({ run }: { run: BenchmarkSummary }) {
  if (run.llm_calls == null || run.expected_llm_calls == null) return null;
  const verified = run.llm_calls === run.expected_llm_calls;
  return (
    <StatusPill tone={verified ? "violet" : "amber"} className="py-0 text-[10px]">
      <BadgeCheck size={11} className="mr-0.5 inline" />
      {verified ? "LLM verified" : `LLM ${run.llm_calls}/${run.expected_llm_calls}`}
    </StatusPill>
  );
}

/** Future-hosted placeholder — the local alternative is the CLI. */
function CloudEvalPlaceholder() {
  return (
    <DashboardCard className="border-dashed">
      <CardTitle icon={CloudUpload}>Submit your strategy for cloud evaluation</CardTitle>
      <p className="text-sm text-[var(--text-muted)]">
        Upload a strategy config or checkpoint and get a scored, reproducible evaluation on
        held-out scenarios — ranked against every other entrant.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button disabled className="eflux-btn h-9 cursor-not-allowed px-4 opacity-50" title="Requires the hosted EFlux service">
          <CloudUpload size={15} />
          Submit for evaluation
        </button>
        <StatusPill tone="amber" className="text-[10px]">Hosted feature — not available in local mode</StatusPill>
      </div>
      <p className="mt-3 flex items-center gap-1.5 text-xs text-[var(--text-subtle)]">
        <Terminal size={13} />
        Local alternative: <code className="rounded bg-[var(--surface-inset)] px-1.5 py-0.5">uv run eflux backtest --months 1</code>
        — results appear on this page.
      </p>
    </DashboardCard>
  );
}

function RunList() {
  const [runs, setRuns] = useState<BenchmarkSummary[] | null>(null);

  useEffect(() => {
    fetchBenchmarks().then(setRuns).catch(() => setRuns([]));
  }, []);

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold text-[var(--text)]">
          <FlaskConical size={22} className="text-[var(--accent)]" />
          Benchmarks
        </h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          Reproducible offline backtests — fixed historical windows, manifest-stamped scenarios,
          strict LLM accounting. The durable counterpart to the live market.
        </p>
      </div>

      <CloudEvalPlaceholder />

      {runs === null ? (
        <EmptyState title="Loading runs..." />
      ) : runs.length === 0 ? (
        <EmptyState
          icon={FlaskConical}
          title="No backtest runs recorded"
          body="Run `uv run eflux backtest` locally; artifacts land in artifacts/backtests/ and show up here."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {runs.map((run) => (
            <Link key={run.run_id} to={`/benchmarks/${run.run_id}`} className="block">
              <DashboardCard className="h-full transition-transform hover:-translate-y-0.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-sm text-[var(--text)]">{run.run_id}</span>
                  <span className="flex items-center gap-1.5">
                    <StatusPill tone={run.market_mode === "realprice" ? "amber" : "accent"} className="py-0 text-[10px]">
                      {run.market_mode}
                    </StatusPill>
                    <StatusBadge status={run.status} />
                    <LlmIntegrityChip run={run} />
                  </span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-[var(--text-muted)] sm:grid-cols-4">
                  <span>Window <b className="text-[var(--text)]">{fmtWindow(run.start, run.end)}</b></span>
                  <span>Ticks <b className="text-[var(--text)]">{run.ticks_run ?? "—"}</b></span>
                  <span>Participants <b className="text-[var(--text)]">{run.live_participants ?? "—"}</b></span>
                  <span>LLM calls <b className="text-[var(--text)]">{run.llm_calls ?? "—"}</b></span>
                </div>
                {run.charts.includes("overview_leaderboard.svg") && (
                  <img
                    src={benchmarkChartUrl(run.run_id, "overview_leaderboard.svg")}
                    alt="run leaderboard"
                    loading="lazy"
                    className="mt-3 w-full rounded-md border border-[var(--border)] bg-white"
                  />
                )}
              </DashboardCard>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

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
      <div className="flex items-center gap-3">
        <Link to="/benchmarks" className="eflux-btn h-9 px-3">
          <ArrowLeft size={15} />
          All runs
        </Link>
        <h1 className="font-mono text-xl font-semibold text-[var(--text)]">{runId}</h1>
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
                      className="w-full rounded-md border border-[var(--border)] bg-white"
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

/** Benchmarks page: list of recorded backtest runs, or one run's scorecard. */
export default function Benchmarks() {
  const { runId } = useParams<{ runId?: string }>();
  return runId ? <RunDetail runId={runId} /> : <RunList />;
}
