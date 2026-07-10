import { useEffect, useState } from "react";
import { ArrowLeft, ListChecks, Trophy } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { fetchCompetition, fetchCompetitionLeaderboard } from "../api/client";
import type { CompetitionDetail as CompetitionDetailData, CompetitionLeaderboard, CompetitionRuleSet } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill, TableShell } from "../components/DashboardCard";

function statusTone(status: string) { return status === "open" ? "success" : status === "closed" ? "muted" : "accent"; }
function configNumber(config: Record<string, unknown>, key: string): string { const value = config[key]; return typeof value === "number" ? String(value) : "—"; }

export default function CompetitionDetail() {
  const { slug = "" } = useParams();
  const [competition, setCompetition] = useState<CompetitionDetailData | null>(null);
  const [leaderboard, setLeaderboard] = useState<CompetitionLeaderboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true); setNotFound(false); setError(null);
    Promise.all([fetchCompetition(slug), fetchCompetitionLeaderboard(slug)])
      .then(([detail, board]) => { setCompetition(detail); setLeaderboard(board); })
      .catch((err: { response?: { status?: number }; message?: string }) => { if (err.response?.status === 404) setNotFound(true); else setError(err.message ?? "Unable to load competition."); })
      .finally(() => setLoading(false));
  }, [slug]);

  if (loading) return <div className="mx-auto w-full max-w-[1400px] px-4 py-5 text-sm text-[var(--text-muted)] md:p-6">Loading competition…</div>;
  if (notFound) return <div className="mx-auto w-full max-w-2xl px-4 py-12 text-center md:p-6"><EmptyState icon={Trophy} title="Competition not found" body="This competition may have been removed or the link is incorrect." /><Link to="/competitions" className="eflux-btn eflux-btn-primary mt-4 h-9 px-4 text-sm">Back to competitions</Link></div>;
  if (error || !competition) return <div className="mx-auto w-full max-w-2xl px-4 py-12 md:p-6"><p className="rounded-lg bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]">{error ?? "Unable to load competition."}</p></div>;

  return <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
    <Link to="/competitions" className="inline-flex items-center gap-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--text)]"><ArrowLeft size={16} /> All competitions</Link>
    <div><div className="flex flex-wrap items-center gap-2"><h1 className="text-xl font-semibold text-[var(--text)]">{competition.title}</h1><StatusPill tone={statusTone(competition.status)}>{competition.status}</StatusPill></div><p className="mt-2 max-w-3xl text-sm text-[var(--text-muted)]">{competition.description}</p></div>
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3"><DashboardCard className="lg:col-span-2"><CardTitle icon={ListChecks}>Rulesets</CardTitle><div className="mb-4 flex flex-wrap gap-2">{competition.tracks.map((track) => <span key={track} className="rounded-full border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--text-muted)]">{track} · <span className="font-mono tabular-nums text-[var(--text)]">{competition.submission_counts[track] ?? 0}</span> submissions</span>)}</div><div className="space-y-3">{competition.rulesets.map((ruleset) => <RuleSet key={ruleset.id} ruleset={ruleset} />)}</div></DashboardCard><DashboardCard><CardTitle icon={Trophy}>Round seeds</CardTitle><dl className="space-y-3 text-sm"><div><dt className="text-xs text-[var(--text-subtle)]">Practice seeds (public)</dt><dd className="mt-1 flex flex-wrap gap-1.5">{competition.practice_seed_values.length ? competition.practice_seed_values.map((seed) => <span key={seed} className="rounded-md bg-[var(--surface-inset)] px-2 py-1 font-mono text-xs tabular-nums text-[var(--text)]">{seed}</span>) : <span className="text-[var(--text-muted)]">None published</span>}</dd></div><div className="flex justify-between gap-3"><dt className="text-[var(--text-muted)]">Hidden seeds</dt><dd className="font-mono tabular-nums text-[var(--text)]">{competition.hidden_seed_count}</dd></div><div className="flex justify-between gap-3"><dt className="text-[var(--text-muted)]">Holdout seeds</dt><dd className="font-mono tabular-nums text-[var(--text)]">{competition.holdout_seed_count}</dd></div></dl></DashboardCard></div>
    <DashboardCard><CardTitle icon={Trophy}>Leaderboard</CardTitle>{!leaderboard || leaderboard.entries.length === 0 ? <EmptyState icon={Trophy} title="No official results yet" body="This competition is collecting evaluation data. Rankings will appear after official runs finish." /> : <TableShell><table className="eflux-table min-w-[760px] text-sm"><thead><tr><th className="px-3 py-2 text-left">Rank</th><th className="px-3 py-2 text-left">Participant</th><th className="px-3 py-2 text-left">Algorithm</th><th className="px-3 py-2 text-right">Score</th><th className="px-3 py-2 text-right">Seeds ok</th><th className="px-3 py-2 text-right">Seeds failed</th></tr></thead><tbody>{leaderboard.entries.map((entry) => <tr key={entry.submission_id}><td className="px-3 py-2 font-mono tabular-nums text-[var(--text)]">{entry.rank}</td><td className="px-3 py-2 text-[var(--text-muted)]">{entry.user_email}</td><td className="px-3 py-2 text-[var(--text)]">{entry.algorithm}</td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">{entry.score.toFixed(4)}</td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--success)]">{entry.seed_ok_count}</td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--danger)]">{entry.seed_failed_count}</td></tr>)}</tbody></table></TableShell>}</DashboardCard>
  </div>;
}

function RuleSet({ ruleset }: { ruleset: CompetitionRuleSet }) {
  const values = [["Window", `${configNumber(ruleset.config, "window_sec")} sec`], ["Deadline", `${configNumber(ruleset.config, "deadline_ms")} ms`], ["Practice", configNumber(ruleset.config, "practice_seeds")], ["Hidden", configNumber(ruleset.config, "hidden_seeds")], ["Holdout", configNumber(ruleset.config, "holdout_seeds")], ["Daily submissions", configNumber(ruleset.config, "submissions_per_day")]];
  return <section className="eflux-inset rounded-lg p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><span className="font-semibold text-[var(--text)]">{ruleset.track}</span><span className="rounded-full border border-[var(--border)] px-2 py-0.5 font-mono text-[11px] text-[var(--text-muted)]">{ruleset.version}</span></div><span className="text-[11px] text-[var(--text-subtle)]">Published {new Date(ruleset.created_at).toLocaleDateString()}</span></div><dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">{values.map(([label, value]) => <div key={label}><dt className="text-[11px] text-[var(--text-subtle)]">{label}</dt><dd className="font-mono text-xs tabular-nums text-[var(--text)]">{value}</dd></div>)}</dl></section>;
}
