import { useEffect, useMemo, useState } from "react";

import { fetchMarketAgents } from "../api/client";
import type { MarketAgent } from "../api/types";
import { CategoryIcon } from "../components/icons";
import { CATEGORY_ORDER, categoryMeta, strategyLabel } from "../lib/categories";
import { formatCompactCount } from "../lib/format";

type SortKey = "name" | "category" | "pnl" | "soc" | "net" | "trades";

/**
 * The market's cast of characters: every built-in VPP with its strategy,
 * endowment and live state. Public — no login needed.
 */
export default function Participants() {
  const [agents, setAgents] = useState<MarketAgent[] | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("category");
  const [sortAsc, setSortAsc] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const a = await fetchMarketAgents();
        if (!cancelled) setAgents(a);
      } catch {
        /* transient — keep the last roster */
      }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const sorted = useMemo(() => {
    if (!agents) return null;
    const catRank = (c: string) => {
      const i = CATEGORY_ORDER.indexOf(c as (typeof CATEGORY_ORDER)[number]);
      return i === -1 ? CATEGORY_ORDER.length : i;
    };
    const val = (a: MarketAgent): number | string => {
      switch (sortKey) {
        case "name":
          return a.name;
        case "category":
          return catRank(a.category);
        case "pnl":
          return Number(a.pnl);
        case "soc":
          return a.soc_frac;
        case "net":
          return a.net_kw;
        case "trades":
          return a.trade_count;
      }
    };
    const out = [...agents].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      const cmp = typeof va === "string" ? va.localeCompare(vb as string) : (va as number) - (vb as number);
      // Stable tiebreak so rows don't jiggle on every 2s refresh.
      return (sortAsc ? cmp : -cmp) || a.name.localeCompare(b.name);
    });
    return out;
  }, [agents, sortKey, sortAsc]);

  const counts = useMemo(() => {
    const c = new Map<string, number>();
    for (const a of agents ?? []) c.set(a.category, (c.get(a.category) ?? 0) + 1);
    return c;
  }, [agents]);

  const onSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(key === "name" || key === "category");
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-white">Market participants</h2>
          <p className="mt-1 text-sm text-slate-400">
            {agents?.length ?? "…"} autonomous VPPs trading right now. Cheap renewables form the
            merit-order floor, batteries arbitrage the middle band, gas sets the top.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {CATEGORY_ORDER.filter((c) => counts.has(c)).map((c) => {
            const meta = categoryMeta(c);
            return (
              <span
                key={c}
                className="flex items-center gap-1.5 rounded-md border border-slate-800 bg-slate-900/60 px-2 py-1 text-xs text-slate-300"
              >
                <CategoryIcon category={c} size={14} style={{ color: meta.color }} />
                <span className="tabular-nums text-slate-200">{counts.get(c)}</span> {meta.label.toLowerCase()}
              </span>
            );
          })}
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-900/40">
        <table className="w-full text-xs">
          <thead className="bg-slate-950/80 text-slate-400">
            <tr>
              <Th label="VPP" onClick={() => onSort("name")} active={sortKey === "name"} asc={sortAsc} align="left" />
              <Th label="Type" onClick={() => onSort("category")} active={sortKey === "category"} asc={sortAsc} align="left" />
              <th className="px-3 py-2 text-left">Strategy</th>
              <th className="px-3 py-2 text-left">Endowment</th>
              <Th label="Output now" onClick={() => onSort("net")} active={sortKey === "net"} asc={sortAsc} align="right" />
              <Th label="Battery SOC" onClick={() => onSort("soc")} active={sortKey === "soc"} asc={sortAsc} align="left" />
              <Th label="PnL ($)" onClick={() => onSort("pnl")} active={sortKey === "pnl"} asc={sortAsc} align="right" />
              <Th label="Trades" onClick={() => onSort("trades")} active={sortKey === "trades"} asc={sortAsc} align="right" />
            </tr>
          </thead>
          <tbody>
            {sorted?.map((a) => (
              <AgentRow key={a.id} agent={a} />
            ))}
            {sorted === null && (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-slate-500">
                  Loading roster…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Th({
  label,
  onClick,
  active,
  asc,
  align,
}: {
  label: string;
  onClick: () => void;
  active: boolean;
  asc: boolean;
  align: "left" | "right";
}) {
  return (
    <th className={`px-3 py-2 ${align === "right" ? "text-right" : "text-left"}`}>
      <button onClick={onClick} className={`hover:text-white ${active ? "text-white" : ""}`}>
        {label}
        {active && <span className="ml-1">{asc ? "▲" : "▼"}</span>}
      </button>
    </th>
  );
}

function AgentRow({ agent: a }: { agent: MarketAgent }) {
  const meta = categoryMeta(a.category);
  const pnl = Number(a.pnl);
  const endowment = [
    a.pv_kw_peak > 0 && `PV ${a.pv_kw_peak}kW`,
    a.wind_kw_rated > 0 && `Wind ${a.wind_kw_rated}kW`,
    a.battery_kwh > 0 && `Batt ${a.battery_kwh}kWh`,
    a.load_kw_base > 0 && `Load ${a.load_kw_base}kW`,
    a.gas_kw_max > 0 && `Gas ${a.gas_kw_max}kW @ ${a.gas_cost_per_kwh}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <tr className={`border-t border-slate-800 ${a.is_llm ? "bg-emerald-950/20" : "hover:bg-slate-800/40"}`}>
      <td className="px-3 py-2 font-medium text-white">
        {a.name}
        {a.is_llm && a.llm_health_state && (
          <span className="ml-2 rounded bg-emerald-900/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-emerald-300">
            LLM {a.llm_health_state}
          </span>
        )}
      </td>
      <td className="px-3 py-2">
        <span
          className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[11px]"
          style={{ backgroundColor: `${meta.color}26`, color: meta.color }}
        >
          <CategoryIcon category={a.category} size={13} />
          {meta.label}
        </span>
      </td>
      <td className="px-3 py-2 text-slate-300">{strategyLabel(a.strategy)}</td>
      <td className="px-3 py-2 text-slate-400">{endowment}</td>
      <td className="px-3 py-2 text-right tabular-nums">
        <NetFlow netKw={a.net_kw} />
      </td>
      <td className="px-3 py-2">
        {a.battery_kwh > 0 ? <SocBar frac={a.soc_frac} /> : <span className="text-slate-600">—</span>}
      </td>
      <td className={`px-3 py-2 text-right tabular-nums ${pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
        {pnl.toFixed(2)}
      </td>
      <td className="px-3 py-2 text-right text-slate-300 tabular-nums" title={`${a.trade_count} trades`}>
        {formatCompactCount(a.trade_count)}
      </td>
    </tr>
  );
}

function NetFlow({ netKw }: { netKw: number }) {
  if (netKw > 0.05) return <span className="text-emerald-300">▲ {netKw.toFixed(2)} kW</span>;
  if (netKw < -0.05) return <span className="text-rose-300">▼ {netKw.toFixed(2)} kW</span>;
  return <span className="text-slate-500">≈ 0 kW</span>;
}

function SocBar({ frac }: { frac: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-800">
        <div className="h-full rounded-full bg-sky-500" style={{ width: `${Math.round(frac * 100)}%` }} />
      </div>
      <span className="text-slate-400 tabular-nums">{(frac * 100).toFixed(0)}%</span>
    </div>
  );
}
