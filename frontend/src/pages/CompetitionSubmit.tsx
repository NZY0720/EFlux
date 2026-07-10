import { useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowLeft, Bot, CheckCircle2, ClipboardCheck, Layers3 } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { createCompetitionSubmission, listVppPresets, type VppPresetConfig } from "../api/competitions";
import { fetchCompetition, listManagedVPPs } from "../api/client";
import type { CompetitionDetail, ManagedVPP } from "../api/types";
import { CardTitle, DashboardCard, EmptyState } from "../components/DashboardCard";

type SourceKind = "vpp" | "preset";

function apiError(err: unknown): { status?: number; message: string } {
  const candidate = err as { response?: { status?: number }; message?: string };
  return { status: candidate.response?.status, message: candidate.message ?? "Unable to submit this entry." };
}

function numberRule(config: Record<string, unknown>, name: string): string {
  const value = config[name];
  return typeof value === "number" ? String(value) : "—";
}

export default function CompetitionSubmit() {
  const { slug = "" } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const requestedVpp = Number(searchParams.get("vpp"));
  const [step, setStep] = useState(1);
  const [competition, setCompetition] = useState<CompetitionDetail | null>(null);
  const [vpps, setVpps] = useState<ManagedVPP[]>([]);
  const [presets, setPresets] = useState<Record<string, VppPresetConfig>>({});
  const [sourceKind, setSourceKind] = useState<SourceKind>("vpp");
  const [vppId, setVppId] = useState<number | null>(null);
  const [preset, setPreset] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true); setError(null);
    Promise.all([fetchCompetition(slug), listManagedVPPs(), listVppPresets()])
      .then(([detail, managed, availablePresets]) => {
        setCompetition(detail); setVpps(managed); setPresets(availablePresets);
        const requested = managed.find((vpp) => vpp.id === requestedVpp);
        if (requested) { setSourceKind("vpp"); setVppId(requested.id); }
        else if (managed.length > 0) setVppId(managed[0].id);
        else {
          const firstPreset = Object.keys(availablePresets)[0] ?? null;
          setSourceKind("preset"); setPreset(firstPreset);
        }
      })
      .catch((err: unknown) => setError(apiError(err).message))
      .finally(() => setLoading(false));
  }, [slug, requestedVpp]);

  const selectedVpp = useMemo(() => vpps.find((vpp) => vpp.id === vppId) ?? null, [vppId, vpps]);
  const selectedPreset = preset ? presets[preset] : null;
  const hasSource = sourceKind === "vpp" ? selectedVpp !== null : selectedPreset !== null;
  const algorithm = sourceKind === "vpp"
    ? selectedVpp?.algorithm ?? "ppo"
    : typeof selectedPreset?.algorithm === "string" ? selectedPreset.algorithm : "ppo";
  const managedRules = competition?.rulesets.find((ruleset) => ruleset.track === "managed");

  const submit = async () => {
    if (!hasSource) return;
    setBusy(true); setError(null);
    try {
      const payload = sourceKind === "vpp"
        ? { algorithm, llm_enabled: false as const, endowment: selectedVpp!.params as Record<string, unknown> }
        : { algorithm, llm_enabled: false as const, preset: preset! };
      const submission = await createCompetitionSubmission(slug, { track: "managed", payload });
      navigate(`/submissions/${submission.id}`);
    } catch (err) {
      const result = apiError(err);
      if (result.status === 422) setError(`This entry cannot be submitted: ${result.message}`);
      else if (result.status === 429) setError("You have reached today’s official submission limit. Please try again tomorrow.");
      else setError(result.message);
    } finally { setBusy(false); }
  };

  if (loading) return <div className="mx-auto w-full max-w-4xl px-4 py-5 text-sm text-[var(--text-muted)] md:p-6">Loading submission form…</div>;
  if (!competition) return <div className="mx-auto w-full max-w-2xl px-4 py-12 md:p-6"><EmptyState icon={ClipboardCheck} title="Competition unavailable" body={error ?? "This competition could not be loaded."} /></div>;

  return <div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-5 md:p-6">
    <Link to={`/competitions/${slug}`} className="inline-flex items-center gap-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--text)]"><ArrowLeft size={16} /> {competition.title}</Link>
    <DashboardCard>
      <CardTitle icon={ClipboardCheck}>Submit to Season 0</CardTitle>
      <p className="mb-5 text-sm text-[var(--text-muted)]">Choose a deterministic managed configuration for an official hidden-seed evaluation.</p>
      <ol className="mb-5 grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">{["Choose source", "Confirm rules", "Submit"].map((label, index) => <li key={label} className={`rounded-md border px-3 py-2 ${step === index + 1 ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]" : "border-[var(--border)] text-[var(--text-muted)]"}`}>{index + 1}. {label}</li>)}</ol>
      {step === 1 && <div className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-2">
          <button type="button" onClick={() => setSourceKind("vpp")} disabled={vpps.length === 0} className={`rounded-lg border p-3 text-left disabled:opacity-50 ${sourceKind === "vpp" ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)]"}`}><Bot size={17} className="mb-2 text-[var(--accent)]" /><span className="block font-semibold text-[var(--text)]">My deployed VPP</span><span className="mt-1 block text-xs text-[var(--text-muted)]">Use its current endowment and prefill its managed algorithm.</span></button>
          <button type="button" onClick={() => setSourceKind("preset")} disabled={Object.keys(presets).length === 0} className={`rounded-lg border p-3 text-left disabled:opacity-50 ${sourceKind === "preset" ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)]"}`}><Layers3 size={17} className="mb-2 text-[var(--accent)]" /><span className="block font-semibold text-[var(--text)]">Preset configuration</span><span className="mt-1 block text-xs text-[var(--text-muted)]">Start from an official managed deployment preset.</span></button>
        </div>
        {sourceKind === "vpp" && <div className="space-y-2">{vpps.length === 0 ? <p className="rounded-lg bg-[var(--surface-inset)] p-3 text-sm text-[var(--text-muted)]">You do not have a deployed managed VPP yet. Choose a preset or deploy one first.</p> : vpps.map((vpp) => <label key={vpp.id} className={`flex cursor-pointer items-center justify-between gap-3 rounded-lg border p-3 ${vpp.id === vppId ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)]"}`}><span><input className="mr-2 accent-[var(--accent)]" type="radio" name="vpp" checked={vpp.id === vppId} onChange={() => setVppId(vpp.id)} />{vpp.name}</span><span className="font-mono text-xs text-[var(--text-muted)]">{vpp.algorithm}</span></label>)}</div>}
        {sourceKind === "preset" && <div className="space-y-2">{Object.entries(presets).map(([name, config]) => <label key={name} className={`flex cursor-pointer items-center justify-between gap-3 rounded-lg border p-3 ${name === preset ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)]"}`}><span><input className="mr-2 accent-[var(--accent)]" type="radio" name="preset" checked={name === preset} onChange={() => setPreset(name)} />{name}</span><span className="font-mono text-xs text-[var(--text-muted)]">{typeof config.algorithm === "string" ? config.algorithm : "ppo"}</span></label>)}</div>}
      </div>}
      {step === 2 && <div className="space-y-4"><div className="eflux-inset rounded-lg p-4"><h2 className="font-semibold text-[var(--text)]">Managed rules · {managedRules?.version ?? "current"}</h2><dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4"><Rule label="Action window" value={`${numberRule(managedRules?.config ?? {}, "window_sec")} sec`} /><Rule label="Deadline" value={`${numberRule(managedRules?.config ?? {}, "deadline_ms")} ms`} /><Rule label="Hidden seeds" value={String(competition.hidden_seed_count)} /><Rule label="Holdout seeds" value={String(competition.holdout_seed_count)} /></dl></div><div className="flex items-start gap-2 rounded-lg bg-[var(--accent-soft)] p-3 text-sm text-[var(--text)]"><CheckCircle2 size={17} className="mt-0.5 shrink-0 text-[var(--accent)]" />Official evaluations are deterministic. LLM agents compete in the live sandbox, so this entry will run with LLM guidance disabled.</div><p className="text-sm text-[var(--text-muted)]">Selected: <span className="font-medium text-[var(--text)]">{sourceKind === "vpp" ? selectedVpp?.name : preset}</span> · algorithm <span className="font-mono text-[var(--text)]">{algorithm}</span></p></div>}
      {step === 3 && <div className="eflux-inset space-y-2 rounded-lg p-4 text-sm"><p className="font-semibold text-[var(--text)]">Ready for official evaluation</p><p className="text-[var(--text-muted)]">Your submission is finalized first. On the next page, choose when to enqueue its hidden-seed evaluation.</p></div>}
      {error && <p className="mt-4 flex items-start gap-2 rounded-lg bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]"><AlertCircle size={17} className="mt-0.5 shrink-0" />{error}</p>}
      <div className="mt-5 flex items-center justify-between gap-2 border-t border-[var(--border)] pt-4"><button type="button" onClick={() => setStep((current) => Math.max(1, current - 1))} disabled={step === 1} className="eflux-btn h-9 px-4 text-sm disabled:opacity-50">Back</button>{step < 3 ? <button type="button" onClick={() => setStep((current) => current + 1)} disabled={!hasSource} className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50">Continue</button> : <button type="button" onClick={submit} disabled={busy || !hasSource || competition.status !== "open"} className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50">{busy ? "Submitting…" : "Create submission"}</button>}</div>
    </DashboardCard>
  </div>;
}

function Rule({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-[var(--text-subtle)]">{label}</dt><dd className="mt-1 font-mono tabular-nums text-[var(--text)]">{value}</dd></div>;
}
