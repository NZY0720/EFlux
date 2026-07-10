import { useEffect, useState, type FormEvent } from "react";
import { FlaskConical, LoaderCircle, Play, Plus, RefreshCw } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { createProveOutRun, listProveOutRuns, type CreateProveOutRun, type ProveOutRunSummary } from "../api/proveout";
import { CardTitle, DashboardCard, EmptyState, StatusPill, TableShell } from "../components/DashboardCard";

const DAY = 86_400_000;
const toDateInput = (date: Date) => date.toISOString().slice(0, 10);
const defaultEnd = toDateInput(new Date(Date.now() - DAY));
const defaultStart = toDateInput(new Date(Date.now() - 7 * DAY));

const statusTone = (status: ProveOutRunSummary["status"]) => status === "done" ? "success" : status === "failed" ? "danger" : status === "running" ? "accent" : "amber";
const formatUsd = (value?: number) => value === undefined ? "—" : `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

export default function ProveOut() {
  const navigate = useNavigate();
  const [label, setLabel] = useState("");
  const [powerMw, setPowerMw] = useState("1");
  const [energyMwh, setEnergyMwh] = useState("4");
  const [efficiency, setEfficiency] = useState("0.9");
  const [cycleCost, setCycleCost] = useState("8");
  const [solarMw, setSolarMw] = useState("2");
  const [cashUsd, setCashUsd] = useState("100000");
  const [startDate, setStartDate] = useState(defaultStart);
  const [endDate, setEndDate] = useState(defaultEnd);
  const [algorithm, setAlgorithm] = useState("heuristic");
  const [runs, setRuns] = useState<ProveOutRunSummary[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRuns = async () => {
    try { setRuns(await listProveOutRuns()); setError(null); }
    catch (err) { setError((err as Error).message || "Unable to load runs."); }
    finally { setLoadingRuns(false); }
  };

  useEffect(() => { void loadRuns(); }, []);
  useEffect(() => {
    if (!runs.some((run) => run.status === "queued" || run.status === "running")) return;
    const timer = window.setInterval(() => { void loadRuns(); }, 3_000);
    return () => window.clearInterval(timer);
  }, [runs]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const days = (Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`)) / DAY + 1;
    if (!Number.isFinite(days) || days < 1 || days > 31) { setError("Choose a date range between 1 and 31 days."); return; }
    const numbers = [powerMw, energyMwh, efficiency, cycleCost, solarMw, cashUsd].map(Number);
    if (numbers.some((value) => !Number.isFinite(value) || value < 0) || Number(powerMw) <= 0 || Number(energyMwh) <= 0 || Number(efficiency) <= 0 || Number(efficiency) > 1) { setError("Enter positive asset values and an efficiency from 0 to 1."); return; }
    const payload: CreateProveOutRun = {
      ...(label.trim() ? { label: label.trim() } : {}),
      endowment: { battery: { power_mw: Number(powerMw), energy_mwh: Number(energyMwh), round_trip_efficiency: Number(efficiency), cycle_cost_per_mwh: Number(cycleCost) }, solar_mw: Number(solarMw), cash_usd: Number(cashUsd) },
      window: { start_date: startDate, end_date: endDate },
      strategy: { algorithm },
    };
    setLaunching(true); setError(null);
    try { const run = await createProveOutRun(payload); await loadRuns(); navigate(`/prove-out/runs/${run.run_id}`); }
    catch (err) { setError((err as Error).message || "Unable to launch prove-out."); }
    finally { setLaunching(false); }
  };

  return <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
    <div><h1 className="flex items-center gap-2 text-2xl font-semibold text-[var(--text)]"><FlaskConical size={22} className="text-[var(--violet)]" /> Prove-out</h1><p className="mt-1 text-sm text-[var(--text-muted)]">Run a private, reproducible strategy evaluation against a bounded historical window.</p></div>
    {error && <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--danger)_35%,transparent)] bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">{error}</p>}
    <DashboardCard><CardTitle icon={Plus}>New run</CardTitle><form onSubmit={submit} className="space-y-5"><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4"><Field label="Run label (optional)"><input value={label} onChange={(event) => setLabel(event.target.value)} className="eflux-input w-full" placeholder="July arbitrage check" /></Field><Field label="Strategy"><select value={algorithm} onChange={(event) => setAlgorithm(event.target.value)} className="eflux-select w-full"><option value="heuristic">Heuristic dispatch</option><option value="baseline">Baseline dispatch</option><option value="conservative">Conservative dispatch</option></select></Field><Field label="Window start"><input required type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} max={endDate} className="eflux-input w-full" /></Field><Field label="Window end"><input required type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} min={startDate} className="eflux-input w-full" /></Field></div><fieldset><legend className="mb-3 text-sm font-semibold text-[var(--text)]">Endowment</legend><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6"><NumberField label="Battery power (MW)" value={powerMw} setValue={setPowerMw} min="0.001" step="0.1" /><NumberField label="Battery energy (MWh)" value={energyMwh} setValue={setEnergyMwh} min="0.001" step="0.1" /><NumberField label="Round-trip efficiency" value={efficiency} setValue={setEfficiency} min="0.01" max="1" step="0.01" /><NumberField label="Cycle cost ($/MWh)" value={cycleCost} setValue={setCycleCost} min="0" step="1" /><NumberField label="Solar (MW)" value={solarMw} setValue={setSolarMw} min="0" step="0.1" /><NumberField label="Cash ($)" value={cashUsd} setValue={setCashUsd} min="0" step="1000" /></div></fieldset><div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] pt-4"><p className="text-xs text-[var(--text-subtle)]">Date ranges are limited to 31 days. Runs are private to your account.</p><button disabled={launching} className="eflux-btn eflux-btn-primary h-10 px-4 disabled:cursor-not-allowed disabled:opacity-60">{launching ? <LoaderCircle size={16} className="animate-spin motion-reduce:animate-none" /> : <Play size={16} />} {launching ? "Launching…" : "Launch prove-out"}</button></div></form></DashboardCard>
    <DashboardCard><CardTitle icon={FlaskConical} action={<button type="button" onClick={() => void loadRuns()} className="eflux-btn h-8 px-2.5 text-xs"><RefreshCw size={14} className={loadingRuns ? "animate-spin motion-reduce:animate-none" : ""} /> Refresh</button>}>Your runs</CardTitle>{runs.length ? <TableShell><table className="eflux-table min-w-[720px] text-sm"><thead><tr><th className="px-3 py-2 text-left">Run</th><th className="px-3 py-2 text-left">Window</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-right">PnL</th><th className="px-3 py-2 text-right">Spread captured</th><th className="px-3 py-2 text-right">Created</th></tr></thead><tbody>{runs.map((run) => <tr key={run.run_id}><td className="px-3 py-2"><Link to={`/prove-out/runs/${run.run_id}`} className="font-medium text-[var(--accent)] hover:underline">{run.label || run.run_id}</Link></td><td className="px-3 py-2 font-mono text-xs text-[var(--text-muted)]">{run.window_start} → {run.window_end}</td><td className="px-3 py-2"><StatusPill tone={statusTone(run.status)}>{run.status === "running" && <LoaderCircle size={12} className="animate-spin motion-reduce:animate-none" />}{run.status}</StatusPill></td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">{formatUsd(run.pnl_usd)}</td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">{run.spread_capture_pct === undefined ? "—" : `${run.spread_capture_pct.toFixed(1)}%`}</td><td className="px-3 py-2 text-right text-xs text-[var(--text-subtle)]">{new Date(run.created_at).toLocaleString()}</td></tr>)}</tbody></table></TableShell> : <EmptyState icon={FlaskConical} title={loadingRuns ? "Loading runs…" : "No prove-outs yet"} body="Launch a private run to compare your strategy with the available price spread." />}</DashboardCard>
  </div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block text-xs font-medium text-[var(--text-muted)]"><span className="mb-1.5 block">{label}</span>{children}</label>; }
function NumberField({ label, value, setValue, min, max, step }: { label: string; value: string; setValue: (value: string) => void; min: string; max?: string; step: string }) { return <Field label={label}><input required type="number" value={value} onChange={(event) => setValue(event.target.value)} min={min} max={max} step={step} className="eflux-input w-full font-mono tabular-nums" /></Field>; }
