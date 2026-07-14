import { useCallback, useEffect, useState } from "react";
import { Bot, Layers3, PlusCircle, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";

import { fetchManagedVPPPerformance, listManagedVPPs, listVPPs } from "../api/client";
import type { ManagedVPP, ManagedVPPPerformance, VPP } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill } from "../components/DashboardCard";
import { algorithmChipLabel, isLlmManaged, LLMBadge } from "./vpps/VppParts";

export default function VppOverview() {
  const [vpps, setVpps] = useState<VPP[]>([]);
  const [managed, setManaged] = useState<ManagedVPP[]>([]);
  const [performance, setPerformance] = useState<Record<number, ManagedVPPPerformance>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [external, agents] = await Promise.all([listVPPs(), listManagedVPPs()]);
      const results = await Promise.all(
        agents
          .filter((agent) => agent.deployment_status !== "failed")
          .map(async (agent) => [agent.id, await fetchManagedVPPPerformance(agent.id)] as const),
      );
      setVpps(external);
      setManaged(agents);
      setPerformance(Object.fromEntries(results));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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
        {loading ? (
          <p className="text-sm text-[var(--text-muted)]" role="status">Loading VPPs…</p>
        ) : error ? (
          <div className="rounded-lg bg-[var(--danger-soft)] p-3" role="alert">
            <p className="text-sm text-[var(--danger)]">{error}</p>
            <button type="button" onClick={() => void load()} className="eflux-btn mt-3 h-8 px-3 text-xs">
              <RefreshCw size={14} /> Retry
            </button>
          </div>
        ) : managed.length === 0 && vpps.length === 0 ? (
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
                          {vpp.deployment_status === "failed" && <StatusPill tone="danger" className="py-0 text-[10px]">failed</StatusPill>}
                        </div>
                        <p className="mt-1 text-xs text-[var(--text-muted)]">PV {vpp.params.pv_kw_peak}kW / Batt {vpp.params.battery_kwh}kWh / Load {vpp.params.load_kw_base}kW</p>
                        {vpp.deployment_error && <p className="mt-1 line-clamp-2 text-xs text-[var(--danger)]">{vpp.deployment_error}</p>}
                      </div>
                      {isLlmManaged(vpp) && <LLMBadge state={vpp.llm_health_state} />}
                    </div>
                    <div className="mt-3 flex items-center justify-between text-xs">
                      <span className="text-[var(--text-subtle)]">PnL</span>
                      <span className={pnl === null ? "text-[var(--text-subtle)]" : pnl >= 0 ? "font-semibold text-[var(--success)]" : "font-semibold text-[var(--danger)]"}>
                        {pnl === null ? (vpp.deployment_status === "failed" ? "Unavailable" : "Loading…") : `$${pnl.toFixed(4)}`}
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
    </div>
  );
}
