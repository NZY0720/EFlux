import { useEffect, useState } from "react";
import { Bot, FlaskConical, History, LockKeyhole, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";

import { fetchBenchmarks } from "../api/client";
import {
  listAgentReleases,
  listReleaseEvaluations,
  type AgentRelease,
  type ReleaseEvaluation,
} from "../api/ecosystem";
import {
  deleteProveOutRun,
  listProveOutRuns,
  type ProveOutRunSummary,
} from "../api/proveout";
import type { BenchmarkSummary } from "../api/types";
import {
  CardTitle,
  DashboardCard,
  EmptyState,
  StatusPill,
  TableShell,
} from "../components/DashboardCard";
import { EvaluationNav } from "../components/WorkspaceNav";
import { useAuth } from "../state/auth";

type ReleaseRun = { release: AgentRelease; evaluation: ReleaseEvaluation };

const statusTone = (status: string) =>
  status === "done" || status === "ok"
    ? "success"
    : status === "failed"
      ? "danger"
      : status === "running"
        ? "accent"
        : "amber";
const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "—";
const formatUsd = (value?: number) =>
  value === undefined
    ? "—"
    : `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const humanize = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

export default function EvaluationRuns() {
  const { email } = useAuth();
  const [privateRuns, setPrivateRuns] = useState<ProveOutRunSummary[]>([]);
  const [releaseRuns, setReleaseRuns] = useState<ReleaseRun[]>([]);
  const [referenceRuns, setReferenceRuns] = useState<BenchmarkSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      const messages: string[] = [];
      const [references, releases, proveOuts] = await Promise.all([
        fetchBenchmarks().catch((err: Error) => {
          messages.push(err.message);
          return [] as BenchmarkSummary[];
        }),
        listAgentReleases()
          .then(async (rows) => {
            const evaluations = await Promise.all(
              rows.map(async (release) => ({
                release,
                evaluations: await listReleaseEvaluations(release.id).catch(() => []),
              })),
            );
            return evaluations.flatMap(({ release, evaluations: items }) =>
              items.map((evaluation) => ({ release, evaluation })),
            );
          })
          .catch((err: Error) => {
            messages.push(err.message);
            return [] as ReleaseRun[];
          }),
        email
          ? listProveOutRuns().catch((err: Error) => {
              messages.push(err.message);
              return [] as ProveOutRunSummary[];
            })
          : Promise.resolve([] as ProveOutRunSummary[]),
      ]);
      if (cancelled) return;
      setReferenceRuns(references);
      setReleaseRuns(
        releases.sort(
          (left, right) =>
            Date.parse(right.evaluation.created_at) - Date.parse(left.evaluation.created_at),
        ),
      );
      setPrivateRuns(proveOuts);
      setError(messages.length ? [...new Set(messages)].join(" · ") : null);
      setLoading(false);
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [email]);

  const removePrivateRun = async (run: ProveOutRunSummary) => {
    if (run.status === "running") return;
    const name = run.label || `Quick test ${run.run_id}`;
    const prompt =
      run.status === "queued"
        ? `Cancel and delete “${name}”?`
        : `Delete “${name}”? Its report and evidence will be removed permanently.`;
    if (!window.confirm(prompt)) return;

    setDeletingId(run.run_id);
    setError(null);
    try {
      await deleteProveOutRun(run.run_id);
      setPrivateRuns((current) =>
        current.filter((item) => item.run_id !== run.run_id),
      );
    } catch (err) {
      setError((err as Error).message || "Unable to delete this quick test.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
      <EvaluationNav />
      <div>
        <h2 className="flex items-center gap-2 text-xl font-semibold text-[var(--text)]">
          <History size={20} className="text-[var(--accent)]" /> Runs
        </h2>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          Private portfolio tests, release-bound evidence and platform reference runs in one place.
        </p>
      </div>

      {error && (
        <p
          role="alert"
          className="rounded-lg bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]"
        >
          Something needs attention: {error}
        </p>
      )}

      <DashboardCard>
        <CardTitle icon={LockKeyhole}>Private quick tests</CardTitle>
        {!email ? (
          <EmptyState
            icon={LockKeyhole}
            title="Sign in to see private runs"
            body="Private portfolio tests are visible only to their owner."
            action={<Link to="/login" className="eflux-btn eflux-btn-primary h-9 px-4 text-sm">Sign in</Link>}
          />
        ) : privateRuns.length === 0 ? (
          <EmptyState
            icon={FlaskConical}
            title={loading ? "Loading private runs…" : "No private runs yet"}
            body="Start with a quick historical test of your portfolio."
            action={<Link to="/evaluate/quick-test" className="eflux-btn eflux-btn-primary h-9 px-4 text-sm">New quick test</Link>}
          />
        ) : (
          <TableShell>
            <table className="eflux-table min-w-[760px] text-sm">
              <thead><tr><th className="px-3 py-2 text-left">Run</th><th className="px-3 py-2 text-left">Window</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-right">PnL</th><th className="px-3 py-2 text-right">Created</th><th className="px-3 py-2 text-right">Actions</th></tr></thead>
              <tbody>{privateRuns.map((run) => <tr key={run.run_id}><td className="px-3 py-2"><Link to={`/evaluate/quick-test/runs/${run.run_id}`} className="font-medium text-[var(--accent)] hover:underline">{run.label || `Quick test ${run.run_id}`}</Link></td><td className="px-3 py-2 font-mono text-xs text-[var(--text-muted)]">{run.window_start} → {run.window_end}</td><td className="px-3 py-2"><StatusPill tone={statusTone(run.status)}>{run.status}</StatusPill></td><td className="px-3 py-2 text-right font-mono tabular-nums">{formatUsd(run.pnl_usd)}</td><td className="px-3 py-2 text-right text-xs text-[var(--text-subtle)]">{formatDate(run.created_at)}</td><td className="px-3 py-2 text-right"><button type="button" onClick={() => void removePrivateRun(run)} disabled={run.status === "running" || deletingId === run.run_id} title={run.status === "running" ? "Wait for this run to finish before deleting it" : "Delete quick test"} className="eflux-btn eflux-btn-danger h-8 px-2.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"><Trash2 size={13} />{deletingId === run.run_id ? "Deleting…" : "Delete"}</button></td></tr>)}</tbody>
            </table>
          </TableShell>
        )}
      </DashboardCard>

      <DashboardCard>
        <CardTitle icon={Bot}>Agent evaluation evidence</CardTitle>
        {releaseRuns.length === 0 ? (
          <EmptyState
            icon={Bot}
            title={loading ? "Loading agent evaluations…" : "No agent evaluations yet"}
            body="Publish an agent release, then attach deterministic, forward or live evidence."
            action={<Link to="/agents" className="eflux-btn h-9 px-4 text-sm">Open Agents</Link>}
          />
        ) : (
          <TableShell>
            <table className="eflux-table min-w-[720px] text-sm">
              <thead><tr><th className="px-3 py-2 text-left">Agent release</th><th className="px-3 py-2 text-left">Evidence type</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Provenance</th><th className="px-3 py-2 text-right">Created</th></tr></thead>
              <tbody>{releaseRuns.map(({ release, evaluation }) => <tr key={`${release.id}-${evaluation.id}`}><td className="px-3 py-2"><div className="flex flex-wrap items-center gap-2"><Link to={`/agents/releases/${release.id}`} className="font-medium text-[var(--accent)] hover:underline">{release.name} · v{release.version}</Link>{release.badges.includes("Built-in Example") && <StatusPill tone="accent" className="py-0 text-[10px]">Built-in example</StatusPill>}</div></td><td className="px-3 py-2 text-[var(--text-muted)]">{humanize(evaluation.kind)}</td><td className="px-3 py-2"><StatusPill tone={statusTone(evaluation.status)}>{evaluation.status}</StatusPill></td><td className="px-3 py-2 text-[var(--text-muted)]">{humanize(evaluation.provenance)}</td><td className="px-3 py-2 text-right text-xs text-[var(--text-subtle)]">{formatDate(evaluation.created_at)}</td></tr>)}</tbody>
            </table>
          </TableShell>
        )}
      </DashboardCard>

      <DashboardCard>
        <CardTitle icon={History}>Platform reference runs</CardTitle>
        {referenceRuns.length === 0 ? (
          <EmptyState
            icon={History}
            title={loading ? "Loading reference runs…" : "No reference runs recorded"}
            body="Local backtest artifacts appear here after a reproducible run completes."
          />
        ) : (
          <TableShell>
            <table className="eflux-table min-w-[700px] text-sm">
              <thead><tr><th className="px-3 py-2 text-left">Run</th><th className="px-3 py-2 text-left">Market</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-right">Participants</th><th className="px-3 py-2 text-right">Finished</th></tr></thead>
              <tbody>{referenceRuns.map((run) => <tr key={run.run_id}><td className="px-3 py-2"><Link to={`/evaluate/runs/${run.run_id}`} className="font-mono text-xs font-medium text-[var(--accent)] hover:underline">{run.run_id}</Link></td><td className="px-3 py-2 text-[var(--text-muted)]">{run.market_mode}</td><td className="px-3 py-2"><StatusPill tone={statusTone(run.status)}>{run.status}</StatusPill></td><td className="px-3 py-2 text-right font-mono tabular-nums">{run.live_participants ?? "—"}</td><td className="px-3 py-2 text-right text-xs text-[var(--text-subtle)]">{formatDate(run.finished_at)}</td></tr>)}</tbody>
            </table>
          </TableShell>
        )}
      </DashboardCard>
    </div>
  );
}
