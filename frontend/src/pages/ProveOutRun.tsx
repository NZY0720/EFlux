import { lazy, Suspense, useEffect, useState } from "react";
import { ArrowLeft, BadgeCheck, Download, FlaskConical, LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { downloadProveOutEvidence, fetchProveOutRun, type ProveOutReport, type ProveOutRunDetail } from "../api/proveout";
import { CardTitle, DashboardCard, EmptyState, StatusPill } from "../components/DashboardCard";

const DailyPnlChart = lazy(() => import("../components/ProveOutDailyPnlChart"));
const money = (value: number) => `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const statusTone = (status: ProveOutRunDetail["status"]) => status === "done" ? "success" : status === "failed" ? "danger" : status === "running" ? "accent" : "amber";

export default function ProveOutRun() {
  const { id = "" } = useParams();
  const [run, setRun] = useState<ProveOutRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => { try { const data = await fetchProveOutRun(id); if (!cancelled) { setRun(data); setError(null); } } catch (err) { if (!cancelled) setError((err as Error).message || "Unable to load prove-out."); } finally { if (!cancelled) setLoading(false); } };
    void load();
    const timer = window.setInterval(() => { if (!cancelled) void load(); }, 3_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [id]);

  if (loading && !run) return <div className="mx-auto flex min-h-56 max-w-[1400px] items-center justify-center px-4 py-5 text-sm text-[var(--text-muted)]"><LoaderCircle size={18} className="mr-2 animate-spin motion-reduce:animate-none" /> Loading prove-out…</div>;
  if (error && !run) return <div className="mx-auto max-w-2xl px-4 py-12 md:p-6"><EmptyState icon={TriangleAlert} title="Unable to load prove-out" body={error} /></div>;
  if (!run) return null;
  const active = run.status === "queued" || run.status === "running";
  const downloadEvidence = async () => {
    setDownloading(true);
    try { await downloadProveOutEvidence(id); }
    catch (err) { setError((err as Error).message || "Unable to download evidence."); }
    finally { setDownloading(false); }
  };
  return <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><Link to="/prove-out" className="inline-flex items-center gap-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--text)]"><ArrowLeft size={16} /> All prove-outs</Link><h1 className="mt-3 flex items-center gap-2 text-2xl font-semibold text-[var(--text)]"><FlaskConical size={22} className="text-[var(--violet)]" /> {run.label || "Prove-out report"}</h1><p className="mt-1 font-mono text-xs text-[var(--text-subtle)]">{run.window_start} → {run.window_end} · {run.run_id}</p></div><div className="flex flex-wrap items-center gap-2">{run.status === "done" && <button type="button" onClick={() => void downloadEvidence()} disabled={downloading} className="eflux-btn h-8 px-3 text-xs"><Download size={14} />{downloading ? "Preparing…" : "Evidence JSON"}</button>}<StatusPill tone={statusTone(run.status)}>{active && <LoaderCircle size={12} className="animate-spin motion-reduce:animate-none" />}{run.status}</StatusPill><StatusPill tone="violet"><ShieldCheck size={13} /> Private — only you can see this</StatusPill></div></div>
    {error && <p role="alert" className="rounded-lg bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">{error}</p>}
    {active && <DashboardCard><div className="flex items-center gap-3 text-sm text-[var(--text-muted)]"><LoaderCircle size={18} className="animate-spin text-[var(--accent)] motion-reduce:animate-none" /><div><span className="font-medium text-[var(--text)]">{run.status === "queued" ? "Queued for evaluation" : "Evaluating your strategy"}</span><p className="mt-0.5 text-xs">This page refreshes automatically while the run is in progress.</p></div></div></DashboardCard>}
    {run.status === "failed" && <DashboardCard><EmptyState icon={TriangleAlert} title="This prove-out failed" body={run.error || "No error detail was returned by the evaluator."} /></DashboardCard>}
    {run.status === "done" && run.report && <Report report={run.report} />}
    {run.status === "done" && !run.report && <DashboardCard><EmptyState icon={TriangleAlert} title="Report is not available" body="The run finished but did not return a report. Please refresh this page." /></DashboardCard>}
  </div>;
}

function Report({ report }: { report: ProveOutReport }) { const tiles = [["PnL", money(report.pnl_usd), report.pnl_usd >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"], ["$/kW-month", money(report.per_kw_month), "text-[var(--text)]"], ["Perfect-foresight spread captured", report.spread_capture_pct === null ? "—" : `${report.spread_capture_pct.toFixed(1)}%`, "text-[var(--accent)]"], ["Max drawdown", money(report.max_drawdown_usd), "text-[var(--danger)]"]] as const; return <><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{tiles.map(([label, value, tone]) => <DashboardCard key={label} className="p-4"><p className="text-xs text-[var(--text-subtle)]">{label}</p><p className={`mt-2 font-mono text-2xl font-semibold tabular-nums ${tone}`}>{value}</p></DashboardCard>)}</div><DashboardCard><CardTitle icon={BadgeCheck}>Daily PnL</CardTitle><div className="lg-solid h-72 p-1"><Suspense fallback={<div className="flex h-full items-center justify-center text-sm text-[var(--text-muted)]">Loading chart…</div>}><DailyPnlChart daily={report.daily} /></Suspense></div></DashboardCard><DashboardCard><CardTitle icon={FlaskConical}>Execution & risk</CardTitle><dl className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-8"><Metric label="Trades" value={String(report.trades)} tone="text-[var(--text)]" /><Metric label="Risk rejections" value={String(report.risk_rejections)} tone="text-[var(--warning)]" /><Metric label="Imbalance penalty" value={money(report.imbalance_penalty_usd)} tone="text-[var(--danger)]" /><Metric label="Battery degradation" value={money(report.degradation_cost_usd)} tone="text-[var(--danger)]" /><Metric label="Ending SOC" value={report.ending_soc_kwh === null ? "—" : `${report.ending_soc_kwh.toFixed(1)} kWh`} tone="text-[var(--text)]" /><Metric label="Audit events" value={String(report.audit_event_count ?? "—")} tone="text-[var(--text)]" /><Metric label="Replay verified" value={report.replay_verified ? "yes" : "no"} tone={report.replay_verified ? "text-[var(--success)]" : "text-[var(--danger)]"} /><Metric label="Days evaluated" value={String(report.days)} tone="text-[var(--text)]" /></dl><p className="mt-4 border-t border-[var(--border)] pt-3 text-xs text-[var(--text-subtle)]">{report.engine} · {report.price_resolution} · evidence {report.evidence_id?.slice(0, 12) ?? "—"}</p></DashboardCard></>; }
function Metric({ label, value, tone }: { label: string; value: string; tone: string }) { return <div><dt className="text-xs text-[var(--text-subtle)]">{label}</dt><dd className={`mt-1 font-mono text-lg font-semibold tabular-nums ${tone}`}>{value}</dd></div>; }
