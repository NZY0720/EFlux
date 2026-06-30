import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BrainCircuit } from "lucide-react";

import { fetchMarketReflections } from "../api/client";
import type { MarketReflection } from "../api/types";
import { EmptyState, StatusPill } from "./DashboardCard";

/**
 * Live LLM guidance feed for the market page — the LLM-steered agents'
 * "thoughts" without needing a login. Newest first, polled every 5s.
 * Several agents share the feed; each gets a stable color badge and the
 * list can be filtered per agent.
 */

// Stable per-agent badge palette: hash of the agent name, so a badge color
// never shifts when other agents enter or slide out of the feed window.
const AGENT_BADGES = [
  "#059669",
  "#0284c7",
  "#7c3aed",
  "#d97706",
  "#e11d48",
  "#0d9488",
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
      color: badgeForName(name),
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
        <span className="text-[var(--text-muted)]">
          <span className="font-medium text-[var(--success)]">{agents.length || "The"} LLM agents</span>{" "}
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
              color={a.color}
              onClick={() => setFilter(filter === a.name ? null : a.name)}
            />
          ))}
        </div>
      )}
      <div className="eflux-inset min-h-0 flex-1 space-y-1.5 overflow-auto rounded-lg p-2">
        {entries === null && <EmptyState icon={BrainCircuit} title="Loading guidance..." className="min-h-full" />}
        {entries !== null && entries.length === 0 && (
          <div className="px-1 py-4 text-center text-xs text-[var(--text-subtle)]">
            No guidance yet. The agents consult the LLM every ~minute when the link is live -
            otherwise they trade on the hybrid PPO baseline. See{" "}
            <Link to="/vpps" className="text-[var(--accent)] hover:underline">
              My VPPs
            </Link>{" "}
            for full performance.
          </div>
        )}
        {visible?.map((r) => (
          <div key={`${r.vpp_id}-${r.ts}`} className="rounded-md border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <AgentBadge name={r.vpp_name} />
              <span className="text-[var(--text-muted)] tabular-nums">
                {new Date(r.ts).toLocaleTimeString("en-GB", { hour12: false })}
              </span>
              {r.ok ? (
                <>
                  <StatusPill tone="success" className="py-0 text-[11px]">ok</StatusPill>
                  <span className="text-[var(--accent)] tabular-nums">{guidanceSummary(r)}</span>
                </>
              ) : (
                <StatusPill tone="danger" className="py-0 text-[11px]">failed</StatusPill>
              )}
            </div>
            <p className="mt-0.5 text-xs text-[var(--text)]">{r.ok ? guidanceText(r) : r.error}</p>
            {r.ok && r.lesson && (
              <p className="mt-0.5 text-[11px] italic text-[var(--text-subtle)]">lesson: {r.lesson}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function HealthSummary({ live, total }: { live: number; total: number }) {
  const tone = live === total ? "success" : live > 0 ? "amber" : "muted";
  return <StatusPill tone={tone}>LLM {live}/{total} live</StatusPill>;
}

function FilterChip({
  label,
  active,
  color,
  onClick,
}: {
  label: string;
  active: boolean;
  color?: string;
  onClick: () => void;
}) {
  const activeStyle = color
    ? { borderColor: `${color}66`, backgroundColor: `${color}1f`, color }
    : undefined;
  return (
    <button
      onClick={onClick}
      style={active ? activeStyle : undefined}
      className={`rounded-full border px-2 py-0.5 transition-colors ${
        active ? "font-medium" : "border-[var(--border)] bg-[var(--surface-muted)] text-[var(--text-subtle)] hover:text-[var(--text)]"
      }`}
    >
      {label}
    </button>
  );
}

function AgentBadge({ name }: { name: string }) {
  const color = badgeForName(name);
  return (
    <span
      className="rounded-full border px-2 py-0.5 font-medium"
      style={{ borderColor: `${color}66`, backgroundColor: `${color}1f`, color }}
    >
      {name}
    </span>
  );
}
