import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Search, UsersRound } from "lucide-react";

import { fetchMarketAgents } from "../api/client";
import type { MarketAgent } from "../api/types";
import { EmptyState, StatusPill, TableShell } from "../components/DashboardCard";
import { CategoryIcon } from "../components/icons";
import { CATEGORY_ORDER, categoryMeta, strategyLabel } from "../lib/categories";
import { formatCompactCount } from "../lib/format";

type SortKey = "name" | "category" | "archetype" | "pnl" | "soc" | "net" | "trades";

const ARCHETYPE_ORDER = ["dispatchable", "arbitrageur", "producer", "consumer", "balanced"];
const RESOURCE_ORDER = ["solar", "battery", "wind", "gas", "load"];

/** The market's cast of characters, with behaviour intentionally separate from assets. */
export default function Participants() {
  const [agents, setAgents] = useState<MarketAgent[] | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("category");
  const [sortAsc, setSortAsc] = useState(true);
  const [query, setQuery] = useState("");
  const [archetypeFilter, setArchetypeFilter] = useState<string | null>(null);
  const [resourceFilter, setResourceFilter] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await fetchMarketAgents();
        if (!cancelled) setAgents(next);
      } catch {
        /* transient, retain the last roster */
      }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const archetypes = useMemo(
    () => ARCHETYPE_ORDER.filter((archetype) => agents?.some((agent) => agent.archetype === archetype)),
    [agents],
  );
  const resources = useMemo(
    () => RESOURCE_ORDER.filter((resource) => agents?.some((agent) => agent.resources.includes(resource))),
    [agents],
  );

  const visible = useMemo(() => {
    if (!agents) return null;
    const categoryRank = (category: string) => {
      const index = CATEGORY_ORDER.indexOf(category as (typeof CATEGORY_ORDER)[number]);
      return index === -1 ? CATEGORY_ORDER.length : index;
    };
    const archetypeRank = (archetype: string) => {
      const index = ARCHETYPE_ORDER.indexOf(archetype);
      return index === -1 ? ARCHETYPE_ORDER.length : index;
    };
    const normalizedQuery = query.trim().toLocaleLowerCase();
    const filtered = agents.filter(
      (agent) =>
        (!normalizedQuery || agent.name.toLocaleLowerCase().includes(normalizedQuery)) &&
        (archetypeFilter === null || agent.archetype === archetypeFilter) &&
        (resourceFilter === null || agent.resources.includes(resourceFilter)),
    );
    const value = (agent: MarketAgent): number | string => {
      switch (sortKey) {
        case "name": return agent.name;
        case "category": return categoryRank(agent.category);
        case "archetype": return archetypeRank(agent.archetype);
        case "pnl": return Number(agent.pnl);
        case "soc": return agent.soc_frac;
        case "net": return agent.net_kw;
        case "trades": return agent.trade_count;
      }
    };
    return [...filtered].sort((a, b) => {
      const aValue = value(a);
      const bValue = value(b);
      const comparison = typeof aValue === "string"
        ? aValue.localeCompare(bValue as string)
        : (aValue as number) - (bValue as number);
      return (sortAsc ? comparison : -comparison) || a.name.localeCompare(b.name);
    });
  }, [agents, archetypeFilter, query, resourceFilter, sortAsc, sortKey]);

  const onSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc((current) => !current);
    else {
      setSortKey(key);
      setSortAsc(key === "name" || key === "category" || key === "archetype");
    }
  };

  const emptyTitle = query || archetypeFilter || resourceFilter
    ? "No participants match these filters"
    : "No participants are available";

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-4 px-4 py-5 md:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-[var(--text)]">
            <UsersRound size={20} className="text-[var(--accent)]" />
            Market participants
          </h2>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            {agents?.length ?? "..."} autonomous VPPs trading right now. Behaviour and owned
            assets are shown separately so a factory with some PV is not called Solar.
          </p>
        </div>
      </div>

      <div className="space-y-2" aria-label="Participant filters">
        <div className="relative max-w-md">
          <label htmlFor="participant-search" className="sr-only">Search participants by name</label>
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-subtle)]" />
          <input
            id="participant-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by VPP name"
            className="w-full rounded-md border border-[var(--border)] bg-[var(--surface)] py-2 pl-9 pr-3 text-sm text-[var(--text)] outline-none transition-colors placeholder:text-[var(--text-subtle)] focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--accent-soft)]"
          />
        </div>
        <FilterChips
          label="Behaviour"
          values={archetypes}
          selected={archetypeFilter}
          onSelect={setArchetypeFilter}
        />
        <FilterChips
          label="Resources"
          values={resources}
          selected={resourceFilter}
          onSelect={setResourceFilter}
        />
      </div>

      <div className="hidden md:block">
        <TableShell>
          <table className="eflux-table min-w-[1220px] text-xs">
            <thead className="sticky top-0 z-10">
              <tr>
                <Th label="VPP" onClick={() => onSort("name")} active={sortKey === "name"} asc={sortAsc} align="left" />
                <Th label="Type" onClick={() => onSort("category")} active={sortKey === "category"} asc={sortAsc} align="left" />
                <Th label="Behaviour" onClick={() => onSort("archetype")} active={sortKey === "archetype"} asc={sortAsc} align="left" />
                <th className="px-3 py-2 text-left font-semibold">Resources</th>
                <th className="px-3 py-2 text-left font-semibold">Strategy</th>
                <th className="px-3 py-2 text-left font-semibold">Endowment</th>
                <Th label="Output now" onClick={() => onSort("net")} active={sortKey === "net"} asc={sortAsc} align="right" />
                <Th label="Battery SOC" onClick={() => onSort("soc")} active={sortKey === "soc"} asc={sortAsc} align="left" />
                <Th label="PnL ($)" onClick={() => onSort("pnl")} active={sortKey === "pnl"} asc={sortAsc} align="right" />
                <Th label="Trades" onClick={() => onSort("trades")} active={sortKey === "trades"} asc={sortAsc} align="right" />
              </tr>
            </thead>
            <tbody>
              {visible?.map((agent) => <AgentRow key={agent.id} agent={agent} />)}
              {visible === null && <TableEmpty colSpan={10} title="Loading roster..." />}
              {visible !== null && visible.length === 0 && <TableEmpty colSpan={10} title={emptyTitle} />}
            </tbody>
          </table>
        </TableShell>
      </div>

      <div className="space-y-3 md:hidden">
        {visible === null && <EmptyState icon={UsersRound} title="Loading roster..." />}
        {visible?.map((agent) => <ParticipantCard key={agent.id} agent={agent} />)}
        {visible !== null && visible.length === 0 && <EmptyState icon={UsersRound} title={emptyTitle} body="Try clearing a filter or searching for another VPP name." />}
      </div>
    </div>
  );
}

function FilterChips({ label, values, selected, onSelect }: { label: string; values: string[]; selected: string | null; onSelect: (value: string | null) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-xs font-medium text-[var(--text-muted)]">{label}</span>
      <button type="button" aria-pressed={selected === null} onClick={() => onSelect(null)} className={`eflux-chip px-2.5 py-1 text-xs ${selected === null ? "eflux-chip-active" : ""}`}>All</button>
      {values.map((value) => (
        <button key={value} type="button" aria-pressed={selected === value} onClick={() => onSelect(selected === value ? null : value)} className={`eflux-chip px-2.5 py-1 text-xs capitalize ${selected === value ? "eflux-chip-active" : ""}`}>
          {value}
        </button>
      ))}
    </div>
  );
}

function Th({ label, onClick, active, asc, align }: { label: string; onClick: () => void; active: boolean; asc: boolean; align: "left" | "right" }) {
  return (
    <th className={`px-3 py-2 ${align === "right" ? "text-right" : "text-left"}`}>
      <button type="button" onClick={onClick} className={`inline-flex items-center gap-1.5 font-semibold transition-colors hover:text-[var(--text)] ${active ? "text-[var(--text)]" : "text-[var(--text-muted)]"} ${align === "right" ? "justify-end" : ""}`}>
        <span>{label}</span>
        {active ? (asc ? <ArrowUp size={13} /> : <ArrowDown size={13} />) : <ArrowUpDown size={13} className="text-[var(--text-subtle)]" />}
      </button>
    </th>
  );
}

function TableEmpty({ colSpan, title }: { colSpan: number; title: string }) {
  return <tr><td colSpan={colSpan} className="p-3"><EmptyState icon={UsersRound} title={title} /></td></tr>;
}

function AgentRow({ agent }: { agent: MarketAgent }) {
  const meta = categoryMeta(agent.category);
  const pnl = Number(agent.pnl);
  return (
    <tr className={agent.is_llm ? "bg-[var(--success-soft)]" : ""}>
      <td className="px-3 py-2 font-medium text-[var(--text)]">{agent.name}{agent.is_llm && agent.llm_health_state && <StatusPill tone={agent.llm_health_state === "live" ? "success" : agent.llm_health_state === "degraded" ? "amber" : "muted"} className="ml-2 py-0 text-[10px] uppercase">LLM {agent.llm_health_state}</StatusPill>}</td>
      <td className="px-3 py-2"><span className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[11px]" style={{ backgroundColor: `${meta.color}26`, color: meta.color }}><CategoryIcon category={agent.category} size={13} />{meta.label}</span></td>
      <td className="px-3 py-2"><ArchetypeBadge archetype={agent.archetype} /></td>
      <td className="px-3 py-2"><ResourceList resources={agent.resources} /></td>
      <td className="px-3 py-2 text-[var(--text-muted)]">{strategyLabel(agent.strategy)}</td>
      <td className="px-3 py-2 text-[var(--text-muted)]">{endowmentText(agent)}</td>
      <td className="px-3 py-2 text-right tabular-nums"><NetFlow netKw={agent.net_kw} /></td>
      <td className="px-3 py-2">{agent.battery_kwh > 0 ? <SocBar frac={agent.soc_frac} /> : <span className="text-[var(--text-subtle)]">-</span>}</td>
      <td className={`px-3 py-2 text-right tabular-nums ${pnl >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>{pnl.toFixed(2)}</td>
      <td className="px-3 py-2 text-right text-[var(--text-muted)] tabular-nums" title={`${agent.trade_count} trades`}>{formatCompactCount(agent.trade_count)}</td>
    </tr>
  );
}

function ParticipantCard({ agent }: { agent: MarketAgent }) {
  const meta = categoryMeta(agent.category);
  const pnl = Number(agent.pnl);
  return (
    <article className={`rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4 ${agent.is_llm ? "bg-[var(--success-soft)]" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><h3 className="truncate font-semibold text-[var(--text)]">{agent.name}</h3><span className="mt-1 inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[11px]" style={{ backgroundColor: `${meta.color}26`, color: meta.color }}><CategoryIcon category={agent.category} size={13} />{meta.label}</span></div>
        <div className={`text-right font-mono text-sm ${pnl >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>{pnl.toFixed(2)}<div className="font-sans text-[10px] text-[var(--text-subtle)]">PnL ($)</div></div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
        <Info label="Behaviour"><ArchetypeBadge archetype={agent.archetype} /></Info>
        <Info label="Resources"><ResourceList resources={agent.resources} /></Info>
        <Info label="Strategy">{strategyLabel(agent.strategy)}</Info>
        <Info label="Output"><NetFlow netKw={agent.net_kw} /></Info>
        <Info label="Endowment" className="col-span-2">{endowmentText(agent)}</Info>
        <Info label="Trades">{formatCompactCount(agent.trade_count)}</Info>
        <Info label="Battery">{agent.battery_kwh > 0 ? `${(agent.soc_frac * 100).toFixed(0)}% SOC` : "No battery"}</Info>
      </dl>
    </article>
  );
}

function Info({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return <div className={className}><dt className="text-[var(--text-subtle)]">{label}</dt><dd className="mt-0.5 text-[var(--text-muted)]">{children}</dd></div>;
}

function ArchetypeBadge({ archetype }: { archetype: string }) {
  return <span className="inline-flex rounded bg-[var(--surface-inset)] px-1.5 py-0.5 text-[11px] font-medium capitalize text-[var(--text)]">{archetype}</span>;
}

function ResourceList({ resources }: { resources: string[] }) {
  return <span className="flex flex-wrap gap-1">{resources.map((resource) => <span key={resource} className="rounded bg-[var(--surface-inset)] px-1.5 py-0.5 text-[10px] capitalize text-[var(--text-muted)]">{resource}</span>)}</span>;
}

function endowmentText(agent: MarketAgent) {
  return [agent.pv_kw_peak > 0 && `PV ${agent.pv_kw_peak}kW`, agent.wind_kw_rated > 0 && `Wind ${agent.wind_kw_rated}kW`, agent.battery_kwh > 0 && `Batt ${agent.battery_kwh}kWh`, agent.load_kw_base > 0 && `Load ${agent.load_kw_base}kW`, agent.gas_kw_max > 0 && `Gas ${agent.gas_kw_max}kW @ ${agent.gas_cost_per_kwh}`].filter(Boolean).join(" · ");
}

function NetFlow({ netKw }: { netKw: number }) {
  if (netKw > 0.05) return <span className="text-[var(--success)]">▲ {netKw.toFixed(2)} kW</span>;
  if (netKw < -0.05) return <span className="text-[var(--danger)]">▼ {netKw.toFixed(2)} kW</span>;
  return <span className="text-[var(--text-subtle)]">~ 0 kW</span>;
}

function SocBar({ frac }: { frac: number }) {
  return <div className="flex items-center gap-2"><div className="h-1.5 w-20 overflow-hidden rounded-md bg-[var(--surface-inset)]"><div className="h-full rounded-md bg-[var(--accent)]" style={{ width: `${Math.round(frac * 100)}%` }} /></div><span className="text-[var(--text-muted)] tabular-nums">{(frac * 100).toFixed(0)}%</span></div>;
}
