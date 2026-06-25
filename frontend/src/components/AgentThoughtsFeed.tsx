import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { fetchMarketReflections } from "../api/client";
import type { MarketReflection } from "../api/types";

/**
 * Live LLM guidance feed for the market page — the LLM-steered agents'
 * "thoughts" without needing a login. Newest first, polled every 5s.
 * Several agents share the feed; each gets a stable color badge and the
 * list can be filtered per agent.
 */

// Stable per-agent badge palette: hash of the agent name, so a badge color
// never shifts when other agents enter or slide out of the feed window.
const AGENT_BADGES = [
  "border-emerald-800 bg-emerald-950/40 text-emerald-300",
  "border-sky-800 bg-sky-950/40 text-sky-300",
  "border-violet-800 bg-violet-950/40 text-violet-300",
  "border-amber-800 bg-amber-950/40 text-amber-300",
  "border-rose-800 bg-rose-950/40 text-rose-300",
  "border-teal-800 bg-teal-950/40 text-teal-300",
];

function badgeForName(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return AGENT_BADGES[Math.abs(hash) % AGENT_BADGES.length];
}

function guidanceSummary(r: MarketReflection): string {
  if (r.risk_budget !== null && r.risk_budget !== undefined) {
    const parts = [`risk ${(r.risk_budget * 100).toFixed(0)}%`];
    if (r.soc_target !== null && r.soc_target !== undefined) {
      parts.push(`SOC ${(r.soc_target * 100).toFixed(0)}%`);
    }
    if (r.preferred_modes?.length) parts.push(`prefer ${r.preferred_modes.slice(0, 2).join(", ")}`);
    return parts.join(" · ");
  }
  if (r.price_adjust !== null && r.price_adjust !== undefined && r.qty_scale !== null && r.qty_scale !== undefined) {
    return `price ${r.price_adjust >= 0 ? "+" : ""}${(r.price_adjust * 100).toFixed(1)}% · qty ×${r.qty_scale.toFixed(2)}`;
  }
  return "guidance updated";
}

function guidanceText(r: MarketReflection): string {
  return r.execution_style || r.rationale || "(no rationale)";
}

interface Props {
  variant?: "p2p" | "realprice";
}

export default function AgentThoughtsFeed({ variant = "p2p" }: Props) {
  const [entries, setEntries] = useState<MarketReflection[] | null>(null);
  const [filter, setFilter] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetchMarketReflections(40);
        if (!cancelled) setEntries(r);
      } catch {
        /* transient — keep showing the last feed */
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Agent roster derived from the feed: name → latest health, stable colors.
  const agents = useMemo(() => {
    const byName = new Map<string, string>();
    for (const r of entries ?? []) {
      if (!byName.has(r.vpp_name)) byName.set(r.vpp_name, r.health_state);
    }
    const names = [...byName.keys()].sort();
    return names.map((name) => ({
      name,
      health: byName.get(name) ?? "offline",
      badge: badgeForName(name),
    }));
  }, [entries]);

  // If the filtered agent's entries all slid out of the feed window, the
  // filter would show an empty list with no chips left to clear it — reset.
  useEffect(() => {
    if (filter !== null && !agents.some((a) => a.name === filter)) setFilter(null);
  }, [agents, filter]);

  const liveCount = agents.filter((a) => a.health === "live").length;
  const visible = entries?.filter((r) => filter === null || r.vpp_name === filter);

  return (
    <div className="flex h-72 flex-col">
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="text-slate-400">
          <span className="font-medium text-emerald-300">{agents.length || "The"} LLM agents</span>{" "}
          {variant === "realprice"
            ? "consult an LLM strategist every ~minute to steer grid-price timing and PPO learning"
            : "consult an LLM strategist every ~minute to bias their trading primitives"}
        </span>
        {agents.length > 0 && <HealthSummary live={liveCount} total={agents.length} />}
      </div>
      {agents.length > 1 && (
        <div className="mb-1.5 flex flex-wrap gap-1 text-[11px]">
          <FilterChip label="All" active={filter === null} onClick={() => setFilter(null)} />
          {agents.map((a) => (
            <FilterChip
              key={a.name}
              label={a.name}
              active={filter === a.name}
              badge={a.badge}
              onClick={() => setFilter(filter === a.name ? null : a.name)}
            />
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto rounded border border-slate-800 bg-slate-950/40 p-2">
        {entries === null && <p className="px-1 py-2 text-center text-xs text-slate-500">Loading…</p>}
        {entries !== null && entries.length === 0 && (
          <p className="px-1 py-4 text-center text-xs text-slate-500">
            No guidance yet. The agents consult the LLM every ~minute when the link is live —
            otherwise they trade on the hybrid PPO baseline. See{" "}
            <Link to="/vpps" className="text-sky-400 hover:text-sky-300">
              My VPPs
            </Link>{" "}
            for full performance.
          </p>
        )}
        {visible?.map((r) => (
          <div key={`${r.vpp_id}-${r.ts}`} className="rounded border border-slate-800/80 bg-slate-900/40 px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className={`rounded border px-1.5 ${badgeForName(r.vpp_name)}`}>{r.vpp_name}</span>
              <span className="text-slate-400 tabular-nums">
                {new Date(r.ts).toLocaleTimeString("en-GB", { hour12: false })}
              </span>
              {r.ok ? (
                <>
                  <span className="rounded bg-emerald-950/60 px-1.5 text-emerald-300">ok</span>
                  <span className="text-sky-300 tabular-nums">{guidanceSummary(r)}</span>
                </>
              ) : (
                <span className="rounded bg-rose-950/60 px-1.5 text-rose-300">failed</span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-300">{r.ok ? guidanceText(r) : r.error}</p>
            {r.ok && r.lesson && (
              <p className="mt-0.5 text-[11px] italic text-slate-500">lesson: {r.lesson}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function HealthSummary({ live, total }: { live: number; total: number }) {
  const style =
    live === total
      ? "border-emerald-800 bg-emerald-950/40 text-emerald-300"
      : live > 0
        ? "border-amber-800 bg-amber-950/40 text-amber-300"
        : "border-slate-700 bg-slate-900 text-slate-400";
  return (
    <span className={`rounded border px-2 py-0.5 ${style}`}>
      LLM {live}/{total} live
    </span>
  );
}

function FilterChip({
  label,
  active,
  badge,
  onClick,
}: {
  label: string;
  active: boolean;
  badge?: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded border px-1.5 py-0.5 transition-colors ${
        active
          ? (badge ?? "border-sky-700 bg-sky-950/60 text-sky-200")
          : "border-slate-800 bg-slate-900/40 text-slate-500 hover:text-slate-300"
      }`}
    >
      {label}
    </button>
  );
}
