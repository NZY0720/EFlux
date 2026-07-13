import { useEffect, useMemo, useState } from "react";
import { BrainCircuit, ChartSpline, Info, Trophy } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { fetchCompetitionLeaderboard, fetchLeaderboard, fetchLeaderboardSessions } from "../api/client";
import type { CompetitionLeaderboard, LeaderboardOut, LeaderboardRow, LeaderboardSession, MarketAgent } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill, TableShell } from "../components/DashboardCard";
import EquityCurves from "../components/EquityCurves";
import LlmPpoInfluencePanel from "../components/LlmPpoInfluencePanel";
import { comparisonReady, endowmentKey, evidenceProgress } from "../lib/arena";
import { CATEGORY_ORDER, categoryMeta } from "../lib/categories";
import { useServerEquity } from "../state/useServerEquity";
import { useStrategyPnl } from "../state/useStrategyPnl";

type Scope = "session" | "alltime";
type SortKey = "score" | "pnl" | "trades" | "hours";
type Track = "live" | "llm-comparison" | "managed" | "container-standard" | "container-model";

const fmtUsd = (s: string) => {
  const n = Number(s);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;
};
const fmtScore = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(3)}`;
const maskEmail = (email: string) => {
  const [local, domain] = email.split("@");
  if (!domain) return `${email.slice(0, 2)}•••`;
  return `${local.slice(0, Math.min(2, local.length))}•••@${domain}`;
};

/**
 * Durable leaderboard — rankings persist across backend restarts (unlike the live
 * dashboards, whose market state is in-memory). "This session" ranks the current
 * boot; "All-time" folds every recorded session of this market mode together.
 * Rows rank by score v1: PnL normalized by endowment size and observed hours, so
 * a big battery doesn't automatically win.
 */
export default function Leaderboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [track, setTrack] = useState<Track>(() =>
    searchParams.get("view") === "llm" ? "llm-comparison" : "live",
  );
  const [scope, setScope] = useState<Scope>("session");
  const [sessions, setSessions] = useState<LeaderboardSession[]>([]);
  const [sessionId, setSessionId] = useState<number | undefined>(undefined);
  const [board, setBoard] = useState<LeaderboardOut | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchLeaderboardSessions().then(setSessions).catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchLeaderboard({
          scope,
          ...(scope === "session" && sessionId !== undefined ? { session_id: sessionId } : {}),
          ...(category ? { category } : {}),
        });
        if (!cancelled) {
          setBoard(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    };
    load();
    const id = setInterval(load, 10_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [scope, sessionId, category]);

  const rows = useMemo(() => {
    const r = [...(board?.rows ?? [])];
    const key: (x: LeaderboardRow) => number =
      sortKey === "pnl"
        ? (x) => Number(x.pnl_usd)
        : sortKey === "trades"
          ? (x) => x.trade_count
          : sortKey === "hours"
            ? (x) => x.hours
            : (x) => x.score;
    r.sort((a, b) => key(b) - key(a));
    return r;
  }, [board, sortKey]);

  // Equity overlay: selected identities (click rows), defaulting to the top 5.
  const chartIdentities = selected.length > 0 ? selected : rows.slice(0, 5).map((r) => r.identity);
  const historySessionId = scope === "session" ? (sessionId ?? board?.session_id ?? undefined) : undefined;
  const equity = useServerEquity(chartIdentities, historySessionId);

  const toggleSelected = (identity: string) =>
    setSelected((cur) =>
      cur.includes(identity) ? cur.filter((i) => i !== identity) : [...cur, identity].slice(-8),
    );

  const selectTrack = (next: Track) => {
    setTrack(next);
    const params = new URLSearchParams(searchParams);
    if (next === "llm-comparison") params.set("view", "llm");
    else params.delete("view");
    setSearchParams(params, { replace: true });
  };

  const th = (label: React.ReactNode, key?: SortKey, align = "text-right") => (
    <th
      className={`px-3 py-2 ${align} font-semibold ${key ? "cursor-pointer select-none hover:text-[var(--text)]" : ""}`}
      onClick={key ? () => setSortKey(key) : undefined}
    >
      {label}
      {key && sortKey === key ? " ▾" : ""}
    </th>
  );

  return (
    <div className="mx-auto w-full max-w-[1400px] space-y-6 px-4 py-5 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold text-[var(--text)]">
            <Trophy size={22} className="text-[var(--warning)]" />
            Leaderboard
          </h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Durable results — survives backend restarts. Ranked by <b>score v1</b>: PnL
            normalized by endowment size and hours observed, so bigger assets don't auto-win.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex overflow-hidden rounded-md border border-[var(--border)]">
            {(["session", "alltime"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setScope(s)}
                className={`px-3 py-1.5 text-sm font-medium ${
                  scope === s
                    ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                    : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)]"
                }`}
              >
                {s === "session" ? "This session" : "All-time"}
              </button>
            ))}
          </div>
          {scope === "session" && sessions.length > 0 && (
            <select
              className="eflux-select rounded-md px-2 py-1.5 text-sm"
              value={sessionId ?? ""}
              onChange={(e) => setSessionId(e.target.value ? Number(e.target.value) : undefined)}
            >
              <option value="">Current session</option>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  #{s.id} · {s.market_mode} · {new Date(s.started_at).toLocaleString()}
                  {s.is_current ? " (running)" : ""}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-1 rounded-lg border border-[var(--border)] bg-[var(--surface-inset)] p-1" role="tablist" aria-label="Leaderboard tracks">
        <TrackTab id="live" active={track} setActive={selectTrack}>Live</TrackTab>
        <TrackTab id="llm-comparison" active={track} setActive={selectTrack}>LLM comparison</TrackTab>
        <TrackTab id="managed" active={track} setActive={selectTrack}>Managed</TrackTab>
        <TrackTab id="container-standard" active={track} setActive={selectTrack}>Container Standard</TrackTab>
        <TrackTab id="container-model" active={track} setActive={selectTrack}>Container Model</TrackTab>
      </div>

      {track === "live" ? <>
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          onClick={() => setCategory(null)}
          className={`eflux-chip ${category === null ? "eflux-chip-active" : ""}`}
        >
          All
        </button>
        {CATEGORY_ORDER.map((c) => {
          const meta = categoryMeta(c);
          return (
            <button
              key={c}
              onClick={() => setCategory(category === c ? null : c)}
              className={`eflux-chip ${category === c ? "eflux-chip-active" : ""}`}
            >
              {meta.label}
            </button>
          );
        })}
      </div>

      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}

      <DashboardCard>
        <CardTitle icon={Trophy}>
          {scope === "session" ? "Session ranking" : `All-time ranking (${board?.market_mode ?? ""})`}
        </CardTitle>
        <TableShell className="max-h-[520px]">
          <table className="eflux-table text-xs">
            <thead className="sticky top-0 z-10">
              <tr>
                {th("#", undefined, "text-left")}
                {th("Agent", undefined, "text-left")}
                {th("Type", undefined, "text-left")}
                {th("Score v1", "score")}
                {th("PnL ($)", "pnl")}
                {th(
                  <span title="Share of the battery-arbitrage profit a perfect-foresight oracle could have captured in the last 24 h">
                    Spread capture
                  </span>,
                )}
                {th("Trades", "trades")}
                {th("Hours", "hours")}
                {scope === "alltime" && th("Sessions")}
                {th("Last seen")}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const meta = categoryMeta(r.category);
                const isSelected = chartIdentities.includes(r.identity);
                return (
                  <tr
                    key={r.identity}
                    onClick={() => toggleSelected(r.identity)}
                    className={`cursor-pointer ${isSelected ? "bg-[var(--accent-soft)]" : ""}`}
                    title="Click to add/remove from the equity chart"
                  >
                    <td className="px-3 py-1.5 text-[var(--text-subtle)] tabular-nums">{i + 1}</td>
                    <td className="px-3 py-1.5 text-[var(--text)]">
                      {r.name}
                      {r.trade_count < 10 && <StatusPill tone="amber" className="ml-2 py-0 text-[10px]">collecting data</StatusPill>}
                      {r.llm_model && (
                        <span className="ml-1.5 text-[10px] text-[var(--violet)]">{r.llm_model}</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5">
                      <StatusPill tone={r.is_llm ? "violet" : "muted"} className="py-0 text-[10px]">
                        {meta.label}
                      </StatusPill>
                    </td>
                    <td
                      className={`px-3 py-1.5 text-right font-semibold tabular-nums ${
                        r.score >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"
                      }`}
                    >
                      {fmtScore(r.score)}
                    </td>
                    <td
                      className={`px-3 py-1.5 text-right tabular-nums ${
                        Number(r.pnl_usd) >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"
                      }`}
                    >
                      {fmtUsd(r.pnl_usd)}
                    </td>
                    <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">
                      {r.spread_capture === null ? "-" : `${Math.round(r.spread_capture * 100)}%`}
                    </td>
                    <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">{r.trade_count}</td>
                    <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">{r.hours.toFixed(1)}</td>
                    {scope === "alltime" && (
                      <td className="px-3 py-1.5 text-right text-[var(--text-muted)] tabular-nums">{r.sessions_count}</td>
                    )}
                    <td className="px-3 py-1.5 text-right text-[var(--text-subtle)] tabular-nums">
                      {new Date(r.last_seen_at).toLocaleTimeString()}
                    </td>
                  </tr>
                );
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={scope === "alltime" ? 10 : 9} className="p-3">
                    <EmptyState
                      icon={Trophy}
                      title="No results recorded yet"
                      body="Results accrue every ~30s while the market runs; they survive restarts."
                    />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </TableShell>
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-[var(--text-subtle)]">
          <Info size={12} />
          Score v1 = PnL ÷ (endowment nameplate power × reference price × hours observed) — the
          fraction of the endowment's flat-out revenue captured as profit.
        </p>
      </DashboardCard>

      {scope === "session" && (
        <DashboardCard>
          <CardTitle icon={ChartSpline}>
            Equity curves — server history (click rows to compare, top 5 by default)
          </CardTitle>
          <EquityCurves history={equity} topN={8} />
        </DashboardCard>
      )}
      </> : track === "llm-comparison" ? <LlmComparison /> : track === "managed" ? <ManagedLeaderboard /> : <LaterPhaseTrack title={track === "container-standard" ? "Container Standard" : "Container Model"} />}
    </div>
  );
}

function LlmComparison() {
  const { agents, arena, history } = useStrategyPnl();
  const [cohortKey, setCohortKey] = useState<string | null>(null);
  const cohorts = useMemo(() => {
    const grouped = new Map<string, MarketAgent[]>();
    for (const agent of agents) {
      if (!agent.is_llm) continue;
      const key = endowmentKey(agent);
      grouped.set(key, [...(grouped.get(key) ?? []), agent]);
    }
    return [...grouped.entries()].filter(([, members]) => members.length >= 2);
  }, [agents]);

  useEffect(() => {
    if (cohorts.length === 0) {
      setCohortKey(null);
      return;
    }
    if (!cohorts.some(([key]) => key === cohortKey)) setCohortKey(cohorts[0][0]);
  }, [cohorts, cohortKey]);

  const contenders = useMemo(() => {
    const members = cohorts.find(([key]) => key === cohortKey)?.[1] ?? [];
    return [...members].sort((left, right) => Number(right.pnl) - Number(left.pnl));
  }, [cohorts, cohortKey]);
  const ready = comparisonReady(contenders, arena);
  const comparisonHistory = useMemo(
    () =>
      Object.fromEntries(
        contenders.map((agent) => [agent.name, history[agent.name] ?? []]),
      ),
    [contenders, history],
  );
  const hasMirrors = agents.some((agent) => agent.mirror_of !== null);

  if (cohorts.length === 0) {
    return (
      <EmptyState
        icon={BrainCircuit}
        title="No comparable LLM cohort yet"
        body="A fair comparison needs at least two LLM-steered agents with exactly the same assets and operating limits."
      />
    );
  }

  return (
    <>
      <DashboardCard>
        <CardTitle icon={BrainCircuit}>Same-asset LLM comparison</CardTitle>
        <p className="mb-4 text-sm text-[var(--text-muted)]">
          Agents are compared only inside an identical-endowment cohort. PnL ranking stays
          hidden until every contender reaches the configured trade and observation minimums.
        </p>
        <div className="mb-4 flex flex-wrap gap-1.5" aria-label="Comparable LLM cohorts">
          {cohorts.map(([key, members], index) => (
            <button
              key={key}
              type="button"
              onClick={() => setCohortKey(key)}
              className={`eflux-chip ${cohortKey === key ? "eflux-chip-active" : ""}`}
            >
              Cohort {index + 1} · {members.length} agents
            </button>
          ))}
        </div>
        <TableShell>
          <table className="eflux-table min-w-[680px] text-sm">
            <thead>
              <tr>
                <th className="px-3 py-2 text-left">Agent</th>
                <th className="px-3 py-2 text-left">Model</th>
                <th className="px-3 py-2 text-right">Trades</th>
                <th className="px-3 py-2 text-right">Observed</th>
                <th className="px-3 py-2 text-right">PnL</th>
              </tr>
            </thead>
            <tbody>
              {contenders.map((agent) => (
                <tr key={agent.id}>
                  <td className="px-3 py-2 font-medium text-[var(--text)]">{agent.name}</td>
                  <td className="px-3 py-2 text-[var(--text-muted)]">
                    {agent.llm_model ?? "unconfigured"}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text-muted)]">
                    {arena ? `${Math.min(agent.trade_count, arena.min_trades)}/${arena.min_trades}` : "…"}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text-muted)]">
                    {arena
                      ? `${Math.min(Math.floor(agent.observation_min), arena.min_observation_min)}/${arena.min_observation_min} min`
                      : "…"}
                  </td>
                  <td className="px-3 py-2 text-right font-mono font-semibold tabular-nums text-[var(--text)]">
                    {ready ? fmtUsd(agent.pnl) : evidenceProgress(agent, arena)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableShell>
      </DashboardCard>

      <DashboardCard>
        <CardTitle icon={ChartSpline}>Cohort equity</CardTitle>
        {ready ? (
          <EquityCurves history={comparisonHistory} topN={8} />
        ) : (
          <p className="text-sm text-[var(--text-muted)]">
            Collecting enough evidence for a fair win-loss comparison.
          </p>
        )}
      </DashboardCard>

      {hasMirrors && (
        <DashboardCard>
          <CardTitle icon={BrainCircuit}>LLM layer vs PPO mirror</CardTitle>
          <LlmPpoInfluencePanel agents={agents} />
        </DashboardCard>
      )}
    </>
  );
}

function TrackTab({ id, active, setActive, children }: { id: Track; active: Track; setActive: (track: Track) => void; children: React.ReactNode }) {
  return <button type="button" role="tab" aria-selected={active === id} onClick={() => setActive(id)} className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${active === id ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"}`}>{children}</button>;
}

function ManagedLeaderboard() {
  const navigate = useNavigate();
  const [board, setBoard] = useState<CompetitionLeaderboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchCompetitionLeaderboard("season-0")
      .then((data) => { if (!cancelled) { setBoard(data); setError(null); } })
      .catch((err: Error) => { if (!cancelled) setError(err.message || "Unable to load the managed leaderboard."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);
  const openCompetition = () => navigate("/competitions/season-0");
  const onRowKeyDown = (event: React.KeyboardEvent<HTMLTableRowElement>) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openCompetition(); } };
  return <DashboardCard><CardTitle icon={Trophy}>Managed ranking</CardTitle>{error ? <p role="alert" className="text-sm text-[var(--danger)]">{error}</p> : loading ? <p className="text-sm text-[var(--text-muted)]">Loading managed results…</p> : !board?.entries.length ? <EmptyState icon={Trophy} title="No managed results yet" body="Managed submissions will appear after their evaluation runs complete." /> : <TableShell><table className="eflux-table min-w-[700px] text-sm"><thead><tr><th className="px-3 py-2 text-left">Rank</th><th className="px-3 py-2 text-left">Participant</th><th className="px-3 py-2 text-left">Algorithm</th><th className="px-3 py-2 text-right">Score</th><th className="px-3 py-2 text-right">Seeds ok</th><th className="px-3 py-2 text-right">Seeds failed</th></tr></thead><tbody>{board.entries.map((entry) => <tr key={entry.submission_id} role="link" tabIndex={0} onClick={openCompetition} onKeyDown={onRowKeyDown} className="cursor-pointer hover:bg-[var(--surface-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--accent)]"><td className="px-3 py-2 font-mono tabular-nums text-[var(--text)]">{entry.rank}</td><td className="px-3 py-2 text-[var(--text-muted)]">{maskEmail(entry.user_email)}</td><td className="px-3 py-2 text-[var(--text)]">{entry.algorithm}</td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--text)]">{entry.score.toFixed(4)}</td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--success)]">{entry.seed_ok_count}</td><td className="px-3 py-2 text-right font-mono tabular-nums text-[var(--danger)]">{entry.seed_failed_count}</td></tr>)}</tbody></table></TableShell>}</DashboardCard>;
}

function LaterPhaseTrack({ title }: { title: string }) { return <DashboardCard><EmptyState icon={Trophy} title={`${title} track`} body="Track opens in a later phase." /></DashboardCard>; }
