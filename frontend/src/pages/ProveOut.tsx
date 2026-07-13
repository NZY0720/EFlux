import { useEffect, useState, type FormEvent } from "react";
import { FlaskConical, LoaderCircle, Play, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { createProveOutRun, deleteProveOutRun, listProveOutRuns, type CreateProveOutRun, type ProveOutRunSummary } from "../api/proveout";
import { CardTitle, DashboardCard, EmptyState, StatusPill, TableShell } from "../components/DashboardCard";
import { EvaluationNav } from "../components/WorkspaceNav";

const DAY = 86_400_000;
const dateInTimeZone = (date: Date, timeZone: string) => {
  const values = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date).map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
};
const shiftDate = (isoDate: string, days: number) => new Date(Date.parse(`${isoDate}T12:00:00Z`) + days * DAY).toISOString().slice(0, 10);
const defaultEnd = shiftDate(dateInTimeZone(new Date(), "America/Los_Angeles"), -1);
const defaultStart = shiftDate(defaultEnd, -6);

const statusTone = (status: ProveOutRunSummary["status"]) => status === "done" ? "success" : status === "failed" ? "danger" : status === "running" ? "accent" : "amber";
const formatUsd = (value?: number) => value === undefined ? "—" : `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

export default function ProveOut() {
  const navigate = useNavigate();
  const [label, setLabel] = useState("");
  const [powerMw, setPowerMw] = useState("1");
  const [energyMwh, setEnergyMwh] = useState("4");
  const [efficiency, setEfficiency] = useState("0.9");
  const [cycleCost, setCycleCost] = useState("8");
  const [solarMw, setSolarMw] = useState("0");
  const [windMw, setWindMw] = useState("0");
  const [windSpeed, setWindSpeed] = useState("7");
  const [loadMw, setLoadMw] = useState("0");
  const [loadProfile, setLoadProfile] = useState<"residential" | "commercial" | "industrial" | "flat" | "ev">("commercial");
  const [cashUsd, setCashUsd] = useState("10000");
  const [startDate, setStartDate] = useState(defaultStart);
  const [endDate, setEndDate] = useState(defaultEnd);
  const [algorithm] = useState("battery_arbitrageur");
  const [runs, setRuns] = useState<ProveOutRunSummary[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [launching, setLaunching] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
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

  const batteryDurationHours = Number(powerMw) > 0 && Number.isFinite(Number(energyMwh) / Number(powerMw))
    ? (Number(energyMwh) / Number(powerMw)).toLocaleString(undefined, { maximumFractionDigits: 2 })
    : "—";

  const removeRun = async (run: ProveOutRunSummary) => {
    if (run.status === "running") return;
    const name = run.label || `Quick test ${run.run_id}`;
    const prompt = run.status === "queued"
      ? `Cancel and delete “${name}”?`
      : `Delete “${name}”? Its report and evidence will be removed permanently.`;
    if (!window.confirm(prompt)) return;
    setDeletingId(run.run_id);
    setError(null);
    try {
      await deleteProveOutRun(run.run_id);
      setRuns((current) => current.filter((item) => item.run_id !== run.run_id));
    } catch (err) {
      setError((err as Error).message || "Unable to delete this quick test.");
    } finally {
      setDeletingId(null);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const days = (Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`)) / DAY + 1;
    if (!Number.isFinite(days) || days < 1 || days > 31) { setError("Choose a date range between 1 and 31 days."); return; }
    const numbers = [powerMw, energyMwh, efficiency, cycleCost, solarMw, windMw, windSpeed, loadMw, cashUsd].map(Number);
    if (numbers.some((value) => !Number.isFinite(value) || value < 0) || Number(powerMw) <= 0 || Number(energyMwh) <= 0 || Number(efficiency) <= 0.5 || Number(efficiency) > 1 || Number(windSpeed) <= 0) { setError("Check the asset values: battery and wind inputs must be positive, and efficiency must be above 0.5 and no more than 1."); return; }
    const payload: CreateProveOutRun = {
      ...(label.trim() ? { label: label.trim() } : {}),
      endowment: {
        battery: { power_mw: Number(powerMw), energy_mwh: Number(energyMwh), round_trip_efficiency: Number(efficiency), cycle_cost_per_mwh: Number(cycleCost) },
        solar_mw: Number(solarMw),
        wind: { power_mw: Number(windMw), mean_speed_mps: Number(windSpeed) },
        load: { base_mw: Number(loadMw), profile: loadProfile, flexibility: 0 },
        cash_usd: Number(cashUsd),
      },
      window: { start_date: startDate, end_date: endDate },
      strategy: { algorithm },
    };
    setLaunching(true); setError(null);
    try { const run = await createProveOutRun(payload); await loadRuns(); navigate(`/evaluate/quick-test/runs/${run.run_id}`); }
    catch (err) { setError((err as Error).message || "Unable to launch quick test."); }
    finally { setLaunching(false); }
  };

  return <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
    <EvaluationNav />
    <div><h2 className="flex items-center gap-2 text-xl font-semibold text-[var(--text)]"><FlaskConical size={20} className="text-[var(--violet)]" /> Quick test</h2><p className="mt-1 text-sm text-[var(--text-muted)]">Privately replay your portfolio through the same order, risk, delivery and settlement path used by the market.</p></div>
    {error && <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--danger)_35%,transparent)] bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">{error}</p>}
    <DashboardCard>
      <CardTitle icon={Plus}>New run</CardTitle>
      <form onSubmit={submit} className="space-y-6">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Field label="Run label (optional)"><input value={label} onChange={(event) => setLabel(event.target.value)} className="eflux-input w-full" placeholder="July arbitrage check" /></Field>
          <Field label="Strategy"><div className="eflux-input flex w-full items-center">Historical battery arbitrage</div></Field>
          <Field label="Window start"><input required type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} max={endDate} className="eflux-input w-full" /></Field>
          <Field label="Window end"><input required type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} min={startDate} max={defaultEnd} className="eflux-input w-full" /></Field>
        </div>

        <fieldset className="rounded-xl border border-[var(--border)] p-4">
          <legend className="px-1 text-sm font-semibold text-[var(--text)]">Battery</legend>
          <p className="mb-4 text-xs text-[var(--text-subtle)]">The default is a battery-only 1 MW / 4 MWh portfolio. Add generation or demand below only if the asset actually has it.</p>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <NumberField label="Power (MW)" value={powerMw} setValue={setPowerMw} min="0.001" step="0.001" />
            <NumberField label="Energy (MWh)" value={energyMwh} setValue={setEnergyMwh} min="0.001" step="0.001" />
            <NumberField label="Round-trip efficiency" value={efficiency} setValue={setEfficiency} min="0.51" max="1" step="0.01" />
            <NumberField label="Cycle cost ($/MWh)" value={cycleCost} setValue={setCycleCost} min="0" step="1" />
          </div>
          <p className="mt-3 text-xs text-[var(--text-subtle)]">At the current values, the battery duration is {batteryDurationHours} hours at full power.</p>
        </fieldset>

        <fieldset className="rounded-xl border border-[var(--border)] p-4">
          <legend className="px-1 text-sm font-semibold text-[var(--text)]">Generation & demand</legend>
          <p className="mb-4 text-xs text-[var(--text-subtle)]">Use zero for resources the portfolio does not own. Solar, wind and load are combined into one net physical position before trading.</p>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <NumberField label="Solar capacity (MW)" value={solarMw} setValue={setSolarMw} min="0" step="0.001" />
            <NumberField label="Wind capacity (MW)" value={windMw} setValue={setWindMw} min="0" step="0.001" />
            <NumberField label="Mean wind speed (m/s)" value={windSpeed} setValue={setWindSpeed} min="0.1" step="0.1" />
            <NumberField label="Base load (MW)" value={loadMw} setValue={setLoadMw} min="0" step="0.001" />
            <Field label="Load profile">
              <select value={loadProfile} onChange={(event) => setLoadProfile(event.target.value as typeof loadProfile)} className="eflux-input w-full">
                <option value="residential">Residential</option>
                <option value="commercial">Commercial</option>
                <option value="industrial">Industrial</option>
                <option value="flat">Flat / 24×7</option>
                <option value="ev">EV charging</option>
              </select>
            </Field>
          </div>
          <p className="mt-3 text-xs text-[var(--text-subtle)]">These are deterministic modeled profiles for comparison, not measured site telemetry. The selected profile and assumptions are recorded in the evidence.</p>
        </fieldset>

        <div className="grid gap-4 md:grid-cols-2">
          <NumberField label="Starting trading cash ($)" value={cashUsd} setValue={setCashUsd} min="0" step="1000" />
          <div className="rounded-lg bg-[var(--surface-raised)] px-3 py-2 text-xs leading-relaxed text-[var(--text-subtle)]">Cash is paper-trading working capital used by order reservation and risk checks; it is not part of the asset's PnL.</div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] pt-4">
          <p className="max-w-3xl text-xs text-[var(--text-subtle)]">Missing CAISO hours are fetched, validated and cached in the background before replay. Hourly LMPs are then repeated across five-minute delivery products. Runs and evidence remain private.</p>
          <button disabled={launching} className="eflux-btn eflux-btn-primary h-10 px-4 disabled:cursor-not-allowed disabled:opacity-60">{launching ? <LoaderCircle size={16} className="animate-spin motion-reduce:animate-none" /> : <Play size={16} />} {launching ? "Launching…" : "Launch quick test"}</button>
        </div>
      </form>
    </DashboardCard>
    <DashboardCard><CardTitle icon={FlaskConical} action={<button type="button" onClick={() => void loadRuns()} className="eflux-btn h-8 px-2.5 text-xs"><RefreshCw size={14} className={loadingRuns ? "animate-spin motion-reduce:animate-none" : ""} /> Refresh</button>}>Your runs</CardTitle>{runs.length ? <TableShell><table className="eflux-table min-w-[800px] text-sm"><thead><tr><th className="px-3 py-2 text-left">Run</th><th className="px-3 py-2 text-left">Window</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-right">PnL</th><th className="px-3 py-2 text-right">Spread captured</th><th className="px-3 py-2 text-right">Created</th><th className="px-3 py-2 text-right">Actions</th></tr></thead><tbody>{runs.map((run) => <tr key={run.run_id}><td className="px-3 py-2"><Link to={`/evaluate/quick-test/runs/${run.run_id}`} className="font-medium text-[var(--accent)] hover:underline">{run.label || run.run_id}</Link></td><td className="px-3 py-2 font-mono text-xs text-[var(--text-muted)]">{run.window_start} → {run.window_end}</td><td className="px-3 py-2"><StatusPill tone={statusTone(run.status)}>{run.status === "running" && <LoaderCircle size={12} className="animate-spin motion-reduce:animate-none" />}{run.status}</StatusPill></td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">{formatUsd(run.pnl_usd)}</td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">{run.spread_capture_pct === undefined ? "—" : `${run.spread_capture_pct.toFixed(1)}%`}</td><td className="px-3 py-2 text-right text-xs text-[var(--text-subtle)]">{new Date(run.created_at).toLocaleString()}</td><td className="px-3 py-2 text-right"><button type="button" onClick={() => void removeRun(run)} disabled={run.status === "running" || deletingId === run.run_id} title={run.status === "running" ? "Wait for this run to finish before deleting it" : "Delete quick test"} className="eflux-btn eflux-btn-danger h-8 px-2.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"><Trash2 size={13} />{deletingId === run.run_id ? "Deleting…" : "Delete"}</button></td></tr>)}</tbody></table></TableShell> : <EmptyState icon={FlaskConical} title={loadingRuns ? "Loading runs…" : "No quick tests yet"} body="Launch a private run to compare your strategy with the available price spread." />}</DashboardCard>
  </div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block text-xs font-medium text-[var(--text-muted)]"><span className="mb-1.5 block">{label}</span>{children}</label>; }
function NumberField({ label, value, setValue, min, max, step }: { label: string; value: string; setValue: (value: string) => void; min: string; max?: string; step: string }) { return <Field label={label}><input required type="number" value={value} onChange={(event) => setValue(event.target.value)} min={min} max={max} step={step} className="eflux-input w-full font-mono tabular-nums" /></Field>; }
