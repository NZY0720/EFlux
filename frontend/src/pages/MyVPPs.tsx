import { useEffect, useState, type FormEvent } from "react";
import {
  AlertCircle,
  BatteryCharging,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Layers3,
  ListChecks,
  MapPinned,
  PlusCircle,
  ShoppingCart,
  Zap,
} from "lucide-react";

import {
  createVPP,
  fetchManagedVPPPerformance,
  listManagedVPPs,
  listVPPs,
  submitOrder,
} from "../api/client";
import type { ManagedVPP, ManagedVPPPerformance, ReflectionEntry, VPP } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill, TableShell } from "../components/DashboardCard";
import { useMarket } from "../state/marketStream";

export default function MyVPPs() {
  const [vpps, setVpps] = useState<VPP[]>([]);
  const [managedVpps, setManagedVpps] = useState<ManagedVPP[]>([]);
  const [selectedManaged, setSelectedManaged] = useState<number | null>(null);
  const [performance, setPerformance] = useState<Record<number, ManagedVPPPerformance>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [pvKw, setPvKw] = useState(6);
  const [battKwh, setBattKwh] = useState(12);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [pvLat, setPvLat] = useState<string>("");
  const [pvLon, setPvLon] = useState<string>("");
  const [pvTilt, setPvTilt] = useState(30);
  const [pvAzimuth, setPvAzimuth] = useState(180);

  const [orderVpp, setOrderVpp] = useState<number | null>(null);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [price, setPrice] = useState(50);
  const [qty, setQty] = useState(0.05);
  const [lastOrder, setLastOrder] = useState<string | null>(null);

  const reload = async () => {
    try {
      const [v, managed] = await Promise.all([listVPPs(), listManagedVPPs()]);
      setVpps(v);
      setManagedVpps(managed);
      if (v.length > 0 && orderVpp === null) setOrderVpp(v[0].id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadPerformance = async (vppId: number) => {
    const data = await fetchManagedVPPPerformance(vppId);
    setPerformance((prev) => ({ ...prev, [vppId]: data }));
  };

  const toggleManaged = async (vppId: number) => {
    const next = selectedManaged === vppId ? null : vppId;
    setSelectedManaged(next);
    if (next !== null) {
      try {
        await loadPerformance(next);
      } catch (e) {
        setError((e as Error).message);
      }
    }
  };

  useEffect(() => {
    if (selectedManaged === null) return;
    const id = setInterval(() => {
      loadPerformance(selectedManaged).catch((e) => setError((e as Error).message));
    }, 2000);
    return () => clearInterval(id);
  }, [selectedManaged]);

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const params: Record<string, number> = { pv_kw_peak: pvKw, battery_kwh: battKwh };
      if (pvLat !== "" && pvLon !== "") {
        params.pv_lat = Number(pvLat);
        params.pv_lon = Number(pvLon);
        params.pv_tilt = pvTilt;
        params.pv_azimuth = pvAzimuth;
      }
      const created = await createVPP(newName.trim(), params);
      setNewName("");
      setOrderVpp(created.id);
      await reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onOrder = async (e: FormEvent) => {
    e.preventDefault();
    if (orderVpp === null) return;
    setBusy(true);
    setError(null);
    setLastOrder(null);
    try {
      const r = await submitOrder({ vpp_id: orderVpp, side, price, qty });
      setLastOrder(`order ${r.order_id} - ${r.trades.length} fill(s), remaining=${r.remaining_qty}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto grid w-full max-w-[1800px] grid-cols-1 gap-6 px-4 py-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)] md:p-6">
      <DashboardCard>
        <CardTitle icon={Layers3}>My VPPs</CardTitle>

        {vpps.length === 0 && managedVpps.length === 0 ? (
          <EmptyState icon={Layers3} title="No VPPs yet" body="Create one below to submit manual market orders." />
        ) : (
          <ul className="space-y-2">
            {managedVpps.map((v) => (
              <li key={v.id} className="eflux-inset overflow-hidden rounded-lg">
                <button
                  type="button"
                  onClick={() => toggleManaged(v.id)}
                  className="flex w-full cursor-pointer items-start justify-between gap-3 px-3 py-3 text-left transition-colors hover:bg-[var(--surface-hover)]"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {selectedManaged === v.id ? (
                        <ChevronDown size={15} className="text-[var(--text-subtle)]" />
                      ) : (
                        <ChevronRight size={15} className="text-[var(--text-subtle)]" />
                      )}
                      <span className="font-medium text-[var(--text)]">{v.name}</span>
                      <StatusPill tone="accent" className="py-0 text-[10px] uppercase">built-in</StatusPill>
                    </div>
                    <div className="mt-1 text-xs text-[var(--text-muted)]">
                      PV {v.params.pv_kw_peak}kW / Batt {v.params.battery_kwh}kWh / Load {v.params.load_kw_base}kW
                    </div>
                    <div className="mt-1 text-xs text-[var(--accent)]">{v.strategy}</div>
                    <div className="mt-1 text-xs text-[var(--text-subtle)]">{v.llm_status}</div>
                  </div>
                  <LLMBadge state={v.llm_health_state} />
                </button>
                {selectedManaged === v.id && <ManagedPerformancePanel data={performance[v.id]} />}
              </li>
            ))}

            {vpps.map((v) => (
              <li key={v.id} className="eflux-inset rounded-lg px-3 py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <div className="min-w-0">
                    <span className="font-medium text-[var(--text)]">{v.name}</span>
                    <span className="ml-2 text-xs text-[var(--text-subtle)]">#{v.id}</span>
                  </div>
                  <StatusPill tone={v.is_active ? "success" : "danger"}>{v.is_active ? "active" : "inactive"}</StatusPill>
                </div>
                <div className="mt-1 text-xs text-[var(--text-muted)]">
                  PV {v.params.pv_kw_peak}kW / Batt {v.params.battery_kwh}kWh / Load {v.params.load_kw_base}kW
                </div>
              </li>
            ))}
          </ul>
        )}

        <form onSubmit={onCreate} className="mt-5 space-y-3 border-t border-[var(--border)] pt-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--text)]">
            <PlusCircle size={16} className="text-[var(--accent)]" />
            Create new VPP
          </h3>
          <input
            placeholder="name"
            required
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="eflux-input w-full rounded-md px-3 py-2 text-sm outline-none"
          />
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <NumberField label="PV peak (kW)" value={pvKw} step="0.5" onChange={setPvKw} />
            <NumberField label="Battery (kWh)" value={battKwh} step="1" onChange={setBattKwh} />
          </div>
          <button
            type="button"
            onClick={() => setAdvancedOpen(!advancedOpen)}
            className="eflux-btn h-8 px-3 text-xs"
          >
            <MapPinned size={14} />
            {advancedOpen ? "Hide advanced" : "Show advanced"}
          </button>
          {advancedOpen && (
            <div className="eflux-inset space-y-2 rounded-lg p-3">
              <p className="text-xs text-[var(--text-muted)]">
                Optional. Fill latitude and longitude to drive PV output from Open-Meteo weather data via pvlib.
              </p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <TextNumberField label="Latitude" value={pvLat} step="0.01" placeholder="22.28" onChange={setPvLat} />
                <TextNumberField label="Longitude" value={pvLon} step="0.01" placeholder="114.13" onChange={setPvLon} />
                <NumberField label="Tilt (deg)" value={pvTilt} step="1" onChange={setPvTilt} />
                <NumberField label="Azimuth (deg from N)" value={pvAzimuth} step="5" onChange={setPvAzimuth} />
              </div>
            </div>
          )}
          <button disabled={busy} className="eflux-btn eflux-btn-primary h-9 px-4 text-sm font-semibold disabled:opacity-50">
            <PlusCircle size={15} />
            {busy ? "Creating..." : "Create"}
          </button>
        </form>
      </DashboardCard>

      <DashboardCard>
        <CardTitle icon={ShoppingCart}>Submit order</CardTitle>
        {vpps.length === 0 ? (
          <EmptyState icon={ShoppingCart} title="Manual orders require an external VPP" />
        ) : (
          <form onSubmit={onOrder} className="space-y-3">
            <label className="block text-xs font-medium text-[var(--text-muted)]">
              VPP
              <select
                value={orderVpp ?? ""}
                onChange={(e) => setOrderVpp(Number(e.target.value))}
                className="eflux-select mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
              >
                {vpps.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} (#{v.id})
                  </option>
                ))}
              </select>
            </label>

            <div>
              <div className="mb-1 text-xs font-medium text-[var(--text-muted)]">Side</div>
              <div className="inline-flex overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]">
                {(["buy", "sell"] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setSide(s)}
                    className={`px-4 py-2 text-sm font-semibold uppercase transition-colors ${
                      side === s
                        ? s === "buy"
                          ? "bg-[var(--success)] text-[var(--text-inverse)]"
                          : "bg-[var(--danger)] text-[var(--text-inverse)]"
                        : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <NumberField label="Price" value={price} step="0.01" onChange={setPrice} />
              <NumberField label="Qty (kWh)" value={qty} step="0.01" onChange={setQty} />
            </div>

            <button
              disabled={busy}
              className={`eflux-btn h-10 w-full px-4 text-sm font-semibold disabled:opacity-50 ${
                side === "buy" ? "eflux-btn-success" : "eflux-btn-danger"
              }`}
            >
              <Zap size={16} />
              {busy ? "Submitting..." : `Submit ${side.toUpperCase()}`}
            </button>

            {lastOrder && (
              <div className="flex items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--success)_42%,transparent)] bg-[var(--success-soft)] px-3 py-2 text-sm text-[var(--success)]">
                <CheckCircle2 size={16} />
                {lastOrder}
              </div>
            )}
          </form>
        )}
      </DashboardCard>

      {error && (
        <div className="lg:col-span-2 flex items-start gap-2 rounded-lg border border-[color-mix(in_srgb,var(--danger)_42%,transparent)] bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]">
          <AlertCircle size={17} className="mt-0.5 shrink-0" />
          {error}
        </div>
      )}
    </div>
  );
}

function NumberField({
  label,
  value,
  step,
  onChange,
}: {
  label: string;
  value: number;
  step: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {label}
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="eflux-input mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
      />
    </label>
  );
}

function TextNumberField({
  label,
  value,
  step,
  placeholder,
  onChange,
}: {
  label: string;
  value: string;
  step: string;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {label}
      <input
        type="number"
        step={step}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="eflux-input mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
      />
    </label>
  );
}

function LLMBadge({ state }: { state: string }) {
  const tone = state === "live" ? "success" : state === "degraded" ? "amber" : "muted";
  const labels: Record<string, string> = {
    live: "LLM live",
    degraded: "LLM degraded",
    offline: "LLM offline",
  };
  return <StatusPill tone={tone}>{labels[state] ?? state}</StatusPill>;
}

function ManagedPerformancePanel({ data }: { data?: ManagedVPPPerformance }) {
  const { nameOf } = useMarket();
  const pnl = Number(data?.pnl ?? 0);
  const pnlClass = pnl >= 0 ? "text-[var(--success)]" : "text-[var(--danger)]";

  return (
    <div className="border-t border-[var(--border)] px-3 py-3">
      {!data ? (
        <EmptyState icon={BrainCircuit} title="Loading performance..." className="min-h-28" />
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Metric label="PnL ($)" value={Number(data.pnl).toFixed(4)} valueClass={pnlClass} />
            <Metric label="Battery SOC" value={`${(data.soc_frac * 100).toFixed(1)}%`} icon={BatteryCharging} />
            <Metric label="Bought (kWh)" value={data.cumulative_energy_bought_kwh.toFixed(4)} />
            <Metric label="Sold (kWh)" value={data.cumulative_energy_sold_kwh.toFixed(4)} />
          </div>
          <ReflectionTimeline data={data} />
          <TableShell className="max-h-72">
            <table className="eflux-table min-w-[720px] text-xs">
              <thead className="sticky top-0 z-10">
                <tr>
                  <th className="px-2 py-2 text-left font-semibold">Time</th>
                  <th className="px-2 py-2 text-left font-semibold">Side</th>
                  <th className="px-2 py-2 text-right font-semibold">Price ($/MWh)</th>
                  <th className="px-2 py-2 text-right font-semibold">Qty (kWh)</th>
                  <th className="px-2 py-2 text-right font-semibold">Cash ($)</th>
                  <th className="px-2 py-2 text-right font-semibold">Counterparty</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_trades.map((t) => (
                  <tr key={`${t.trade_id}-${t.side}`}>
                    <td className="px-2 py-1.5 text-[var(--text-muted)] tabular-nums">
                      {new Date(t.wall_ts).toLocaleTimeString("en-GB", { hour12: false })}
                    </td>
                    <td className={t.side === "buy" ? "px-2 py-1.5 text-[var(--success)]" : "px-2 py-1.5 text-[var(--danger)]"}>
                      {t.side}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[var(--text)] tabular-nums">{Number(t.price).toFixed(2)}</td>
                    <td className="px-2 py-1.5 text-right text-[var(--text)] tabular-nums">{Number(t.qty).toFixed(4)}</td>
                    <td className="px-2 py-1.5 text-right text-[var(--text)] tabular-nums">{Number(t.cash).toFixed(4)}</td>
                    <td className="px-2 py-1.5 text-right text-[var(--text-muted)]">
                      {t.counterparty ?? nameOf(t.counterparty_vpp_id)}
                    </td>
                  </tr>
                ))}
                {data.recent_trades.length === 0 && (
                  <tr>
                    <td colSpan={6} className="p-3">
                      <EmptyState icon={ListChecks} title="No trades yet" className="min-h-24" />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableShell>
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  valueClass = "text-[var(--text)]",
  icon: Icon,
}: {
  label: string;
  value: string;
  valueClass?: string;
  icon?: typeof BatteryCharging;
}) {
  return (
    <div className="eflux-inset rounded-md px-2 py-2">
      <div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-subtle)]">
        {Icon && <Icon size={12} />}
        {label}
      </div>
      <div className={`mt-1 text-sm font-semibold tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
}

function guidanceSummary(r: ReflectionEntry): string {
  if (r.risk_budget !== null && r.risk_budget !== undefined) {
    const parts = [`risk ${(r.risk_budget * 100).toFixed(0)}%`];
    if (r.soc_target !== null && r.soc_target !== undefined) {
      parts.push(`SOC ${(r.soc_target * 100).toFixed(0)}%`);
    }
    if (r.preferred_modes?.length) parts.push(`prefer ${r.preferred_modes.slice(0, 2).join(", ")}`);
    return parts.join(" / ");
  }
  if (r.price_adjust !== null && r.price_adjust !== undefined && r.qty_scale !== null && r.qty_scale !== undefined) {
    return `price ${r.price_adjust >= 0 ? "+" : ""}${(r.price_adjust * 100).toFixed(1)}% / qty x${r.qty_scale.toFixed(2)}`;
  }
  return "guidance updated";
}

function guidanceText(r: ReflectionEntry): string {
  return r.execution_style || r.rationale || "(no rationale)";
}

function ReflectionTimeline({ data }: { data: ManagedVPPPerformance }) {
  const { reflections, llm_health: health } = data;
  if (health === null && reflections.length === 0) return null;

  return (
    <div>
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-subtle)]">Guidance timeline</h4>
        {health && (
          <span className="text-[11px] text-[var(--text-subtle)]">
            {health.ok_count} ok / {health.fail_count} failed
            {health.last_ok_ts &&
              ` / last ok ${new Date(health.last_ok_ts).toLocaleTimeString("en-GB", { hour12: false })}`}
          </span>
        )}
      </div>
      <div className="eflux-inset max-h-56 space-y-1.5 overflow-auto rounded-lg p-2">
        {reflections.length === 0 && (
          <p className="px-1 py-2 text-center text-xs text-[var(--text-subtle)]">
            No guidance yet - the agent consults the LLM every ~minute.
          </p>
        )}
        {reflections.map((r) => (
          <div key={r.ts} className="rounded-md border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
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
          </div>
        ))}
      </div>
    </div>
  );
}
