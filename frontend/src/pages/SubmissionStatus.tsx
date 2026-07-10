import { useCallback, useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, ClipboardCheck, Play, Trophy } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { enqueueSubmissionEvaluation, fetchSubmission, type EvaluationRun, type SubmissionDetail } from "../api/competitions";
import { listCompetitions } from "../api/client";
import type { CompetitionListItem } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill } from "../components/DashboardCard";

function apiError(err: unknown): { status?: number; message: string } {
  const candidate = err as { response?: { status?: number }; message?: string };
  return { status: candidate.response?.status, message: candidate.message ?? "Unable to update this evaluation." };
}

function active(run: EvaluationRun | null): boolean { return run?.status === "queued" || run?.status === "running"; }
function tone(status: string): "success" | "danger" | "amber" | "accent" | "muted" {
  if (status === "ok" || status === "scored") return "success";
  if (status === "participant_failure" || status === "infra_failure" || status === "failed") return "danger";
  if (status === "queued" || status === "running") return "amber";
  return "muted";
}

export default function SubmissionStatus() {
  const { id = "" } = useParams();
  const submissionId = Number(id);
  const [submission, setSubmission] = useState<SubmissionDetail | null>(null);
  const [competition, setCompetition] = useState<CompetitionListItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!Number.isInteger(submissionId) || submissionId <= 0) { setLoading(false); return; }
    try {
      const detail = await fetchSubmission(submissionId);
      setSubmission(detail);
      const competitions = await listCompetitions();
      setCompetition(competitions.find((item) => item.id === detail.competition_id) ?? null);
      setError(null);
    } catch (err) { setError(apiError(err).message); }
    finally { setLoading(false); }
  }, [submissionId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!active(submission?.latest_run ?? null)) return;
    const timer = window.setInterval(() => { void load(); }, 2000);
    return () => window.clearInterval(timer);
  }, [submission?.latest_run?.status, load]);

  const evaluate = async () => {
    if (!submission) return;
    setBusy(true); setError(null);
    try {
      const run = await enqueueSubmissionEvaluation(submission.id);
      setSubmission({ ...submission, latest_run: run });
    } catch (err) {
      const result = apiError(err);
      setError(result.status === 409 ? "An evaluation is already queued or running. This page will keep updating its progress." : result.message);
      if (result.status === 409) void load();
    } finally { setBusy(false); }
  };

  if (loading) return <div className="mx-auto w-full max-w-4xl px-4 py-5 text-sm text-[var(--text-muted)] md:p-6">Loading submission…</div>;
  if (!submission) return <div className="mx-auto w-full max-w-2xl px-4 py-12 md:p-6"><EmptyState icon={ClipboardCheck} title="Submission unavailable" body={error ?? "This submission does not exist or is not available to your account."} /></div>;
  const run = submission.latest_run;
  const algorithm = typeof submission.payload.algorithm === "string" ? submission.payload.algorithm : "—";
  const source = typeof submission.payload.preset === "string" ? `Preset · ${submission.payload.preset}` : submission.payload.endowment ? "Deployed VPP endowment" : "—";

  return <div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-5 md:p-6">
    <Link to={competition ? `/competitions/${competition.slug}` : "/competitions"} className="inline-flex items-center gap-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--text)]"><ArrowLeft size={16} /> {competition?.title ?? "Competitions"}</Link>
    <DashboardCard><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle icon={ClipboardCheck}>Submission #{submission.id}</CardTitle><p className="mt-1 text-sm text-[var(--text-muted)]">Official managed-track entry</p></div><StatusPill tone={tone(run?.status ?? submission.status)}>{run?.status ?? submission.status}</StatusPill></div><dl className="mt-5 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4"><Detail label="Algorithm" value={algorithm} /><Detail label="Source" value={source} plain /><Detail label="Created" value={new Date(submission.created_at).toLocaleString()} plain /><Detail label="Rules" value={run?.rules_version ?? "Not queued"} /></dl></DashboardCard>
    <DashboardCard><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle icon={Play}>Official evaluation</CardTitle><p className="mt-1 text-sm text-[var(--text-muted)]">{active(run) ? "The page refreshes while hidden seeds are being evaluated." : run ? "This is the latest official evaluation for this submission." : "No official evaluation has been queued yet."}</p></div><button type="button" onClick={evaluate} disabled={busy || active(run)} className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50"><Play size={15} />{busy ? "Queueing…" : active(run) ? "Evaluation active" : "Evaluate"}</button></div>{run && <><div className="mt-5 flex flex-wrap items-center gap-2"><span className="text-sm text-[var(--text-muted)]">Final score</span><span className="font-mono text-lg tabular-nums text-[var(--text)]">{run.score === null ? "Pending" : run.score.toFixed(4)}</span>{run.score !== null && competition && <Link to={`/competitions/${competition.slug}`} className="ml-auto inline-flex items-center gap-1 text-sm text-[var(--accent)] hover:underline"><Trophy size={15} /> View leaderboard</Link>}</div><SeedGrid seeds={run.seed_runs} /></>}</DashboardCard>
    {error && <p className="flex items-start gap-2 rounded-lg bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]"><AlertCircle size={17} className="mt-0.5 shrink-0" />{error}</p>}
  </div>;
}

function Detail({ label, value, plain = false }: { label: string; value: string; plain?: boolean }) {
  return <div><dt className="text-xs text-[var(--text-subtle)]">{label}</dt><dd className={`mt-1 text-sm text-[var(--text)] ${plain ? "" : "font-mono tabular-nums"}`}>{value}</dd></div>;
}

function SeedGrid({ seeds }: { seeds: EvaluationRun["seed_runs"] }) {
  return <div className="mt-5"><h2 className="text-sm font-semibold text-[var(--text)]">Hidden seeds</h2><div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{seeds.map((seed) => <div key={seed.seed_label} className="rounded-lg border border-[var(--border)] bg-[var(--surface-inset)] px-3 py-2"><div className="flex items-center justify-between gap-2"><span className="font-mono text-xs tabular-nums text-[var(--text)]">{seed.seed_label}</span><StatusPill tone={tone(seed.status)} className="py-0 text-[10px]">{seed.status}</StatusPill></div><div className="mt-2 font-mono text-xs tabular-nums text-[var(--text-muted)]">{seed.score === null ? `attempt ${seed.attempt}` : `score ${seed.score.toFixed(4)}`}</div></div>)}</div></div>;
}
