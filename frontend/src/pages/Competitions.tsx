import { useEffect, useState } from "react";
import { ChevronRight, Trophy } from "lucide-react";
import { Link } from "react-router-dom";

import { listCompetitions } from "../api/client";
import type { CompetitionListItem } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill } from "../components/DashboardCard";

function statusTone(status: string) {
  return status === "open" ? "success" : status === "closed" ? "muted" : "accent";
}

export default function Competitions() {
  const [competitions, setCompetitions] = useState<CompetitionListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listCompetitions().then(setCompetitions).catch((err: Error) => setError(err.message)).finally(() => setLoading(false));
  }, []);

  return <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
    <div><h1 className="text-xl font-semibold text-[var(--text)]">Competitions</h1><p className="mt-1 text-sm text-[var(--text-muted)]">Compare agents over published rules and independently evaluated seeds.</p></div>
    <DashboardCard><CardTitle icon={Trophy}>Open catalogue</CardTitle>
      {loading ? <p className="text-sm text-[var(--text-muted)]">Loading competitions…</p> : error ? <p className="rounded-lg bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]">{error}</p> : competitions.length === 0 ? <EmptyState icon={Trophy} title="No competitions available" body="Check back when the next evaluation round opens." /> : <ul className="space-y-3">{competitions.map((competition) => {
        const submissions = Object.values(competition.submission_counts).reduce((total, count) => total + count, 0);
        return <li key={competition.id}><Link to={`/competitions/${competition.slug}`} className="eflux-inset flex items-center justify-between gap-4 rounded-lg p-4 transition-colors hover:bg-[var(--surface-hover)]"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold text-[var(--text)]">{competition.title}</h2><StatusPill tone={statusTone(competition.status)}>{competition.status}</StatusPill></div><div className="mt-2 flex flex-wrap gap-2">{competition.tracks.map((track) => <span key={track} className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--text-muted)]">{track} · <span className="font-mono tabular-nums">{competition.submission_counts[track] ?? 0}</span></span>)}</div><p className="mt-2 text-xs text-[var(--text-subtle)]"><span className="font-mono tabular-nums">{submissions}</span> total submission{submissions === 1 ? "" : "s"}</p></div><ChevronRight size={18} className="shrink-0 text-[var(--text-subtle)]" /></Link></li>;
      })}</ul>}
    </DashboardCard>
  </div>;
}
