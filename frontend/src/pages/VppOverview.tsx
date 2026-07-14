import { useEffect, useState } from "react";
import { Bot, Layers3, PlusCircle } from "lucide-react";
import { Link } from "react-router-dom";

import { fetchManagedVPPPerformance, listManagedVPPs, listVPPs } from "../api/client";
import type { ManagedVPP, ManagedVPPPerformance, VPP } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill } from "../components/DashboardCard";
import { algorithmChipLabel, isLlmManaged, LLMBadge } from "./vpps/VppParts";

export default function VppOverview() {
  const [vpps, setVpps] = useState<VPP[]>([]);
  const [managed, setManaged] = useState<ManagedVPP[]>([]);
  const [performance, setPerformance] = useState<Record<number, ManagedVPPPerformance>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([listVPPs(), listManagedVPPs()])
      .then(async ([external, agents]) => {
        setVpps(external);
        setManaged(agents);
        const results = await Promise.all(
          agents.map(async (agent) => [agent.id, await fetchManagedVPPPerformance(agent.id)] as const),
        );
        setPerformance(Object.fromEntries(results));
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-6 px-4 py-5 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text)]">My VPPs</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">Your deployed fleet and paper-trading status.</p>
        </div>
        <Link to="/vpps/new" className="eflux-btn eflux-btn-primary h-9 px-4 text-sm font-semibold">
          <PlusCircle size={16} /> Deploy new
        </Link>
      </div>

      <DashboardCard>
        <CardTitle icon={Layers3}>Fleet overview</CardTitle>
        {managed.length === 0 && vpps.length === 0 ? (
          <EmptyState icon={Layers3} title="No VPPs yet" body="Deploy an agent or create a manual VPP to get started." />
        ) : (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {managed.map((vpp) => {
              const data = performance[vpp.id];
              const pnl = data ? Number(data.pnl) : null;
              return (
                <li key={`managed-${vpp.id}`}>
                  <Link to={`/vpps/${vpp.id}`} className="eflux-inset block rounded-lg p-3 transition-colors hover:bg-[var(--surface-hover)]">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <Bot size={15} className="text-[var(--accent)]" />
                          <span className="truncate font-medium text-[var(--text)]">{vpp.name}</span>
                          <StatusPill tone="accent" className="py-0 text-[10px]">{algorithmChipLabel(vpp)}</StatusPill>
                        </div>
                        <p className="mt-1 text-xs text-[var(--text-muted)]">PV {vpp.params.pv_kw_peak}kW / Batt {vpp.params.battery_kwh}kWh / Load {vpp.params.load_kw_base}kW</p>
                      </div>
                      {isLlmManaged(vpp) && <LLMBadge state={vpp.llm_health_state} />}
                    </div>
                    <div className="mt-3 flex items-center justify-between text-xs">
                      <span className="text-[var(--text-subtle)]">PnL</span>
                      <span className={pnl === null ? "text-[var(--text-subtle)]" : pnl >= 0 ? "font-semibold text-[var(--success)]" : "font-semibold text-[var(--danger)]"}>
                        {pnl === null ? "Loading…" : `$${pnl.toFixed(4)}`}
                      </span>
                    </div>
                  </Link>
                </li>
              );
            })}
            {vpps.map((vpp) => (
              <li key={`external-${vpp.id}`}>
                <Link to={`/vpps/${vpp.id}`} className="eflux-inset block rounded-lg p-3 transition-colors hover:bg-[var(--surface-hover)]">
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate font-medium text-[var(--text)]">{vpp.name}</span>
                    <StatusPill tone={vpp.is_active ? "success" : "danger"}>{vpp.is_active ? "active" : "inactive"}</StatusPill>
                  </div>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">PV {vpp.params.pv_kw_peak}kW / Batt {vpp.params.battery_kwh}kWh / Load {vpp.params.load_kw_base}kW</p>
                  <p className="mt-3 text-xs text-[var(--text-subtle)]">Manual trading VPP · #{vpp.id}</p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </DashboardCard>
      {error && <p className="rounded-lg bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]">{error}</p>}
    </div>
  );
}
