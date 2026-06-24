import { useEffect, useState } from "react";

import {
  createVPP,
  fetchManagedVPPPerformance,
  listManagedVPPs,
  listVPPs,
  submitOrder,
} from "../api/client";
import type { ManagedVPP, ManagedVPPPerformance, ReflectionEntry, VPP } from "../api/types";
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
  // Optional PV physical-model geometry (lat/lon enables Open-Meteo + pvlib path).
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

  const onCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const params: Record<string, number> = { pv_kw_peak: pvKw, battery_kwh: battKwh };
      // Only include geo if both lat and lon are filled — backend treats either-null as "use stub model".
      if (pvLat !== "" && pvLon !== "") {
        params.pv_lat = Number(pvLat);
        params.pv_lon = Number(pvLon);
        params.pv_tilt = pvTilt;
        params.pv_azimuth = pvAzimuth;
      }
      const created = await createVPP(newName.trim(), params);
      setNewName("");
      setOrderVpp(created.id); // pre-select the new VPP in the order form
      await reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onOrder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (orderVpp === null) return;
    setBusy(true);
    setError(null);
    setLastOrder(null); // clear stale feedback from the previous order
    try {
      const r = await submitOrder({ vpp_id: orderVpp, side, price, qty });
      setLastOrder(`order ${r.order_id} — ${r.trades.length} fill(s), remaining=${r.remaining_qty}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
      <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <h2 className="text-sm uppercase tracking-wide text-slate-400 mb-3">My VPPs</h2>
        {vpps.length === 0 && managedVpps.length === 0 && <p className="text-slate-500">No VPPs yet — create one →</p>}
        <ul className="space-y-2">
          {managedVpps.map((v) => (
            <li key={v.id} className="rounded border border-sky-800 bg-sky-950/30 p-3">
              <button
                type="button"
                onClick={() => toggleManaged(v.id)}
                className="w-full cursor-pointer text-left hover:bg-sky-900/20"
              >
              <div className="flex items-baseline justify-between gap-3">
                <div>
                  <span className="text-slate-500">{selectedManaged === v.id ? "▾" : "▸"}</span>
                  <span className="ml-1 text-white font-medium">{v.name}</span>
                  <span className="ml-2 rounded bg-sky-900/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-sky-300">
                    built-in
                  </span>
                  <span className="ml-2 text-[11px] text-slate-500">
                    {selectedManaged === v.id ? "" : "click for performance & reflections"}
                  </span>
                </div>
                <LLMBadge state={v.llm_health_state} />
              </div>
              <div className="mt-1 text-xs text-slate-400">
                PV {v.params.pv_kw_peak}kW · Batt {v.params.battery_kwh}kWh · Load {v.params.load_kw_base}kW
              </div>
              <div className="mt-1 text-xs text-sky-300">{v.strategy}</div>
              <div className="mt-1 text-xs text-slate-500">{v.llm_status}</div>
              </button>
              {selectedManaged === v.id && (
                <ManagedPerformancePanel data={performance[v.id]} />
              )}
            </li>
          ))}
          {vpps.map((v) => (
            <li key={v.id} className="rounded border border-slate-800 bg-slate-950/60 p-3">
              <div className="flex items-baseline justify-between">
                <div>
                  <span className="text-white font-medium">{v.name}</span>
                  <span className="ml-2 text-xs text-slate-500">#{v.id}</span>
                </div>
                <span className={`text-xs ${v.is_active ? "text-emerald-400" : "text-rose-400"}`}>
                  {v.is_active ? "active" : "inactive"}
                </span>
              </div>
              <div className="mt-1 text-xs text-slate-400">
                PV {v.params.pv_kw_peak}kW · Batt {v.params.battery_kwh}kWh · Load {v.params.load_kw_base}kW
              </div>
            </li>
          ))}
        </ul>

        <form onSubmit={onCreate} className="mt-4 space-y-3 border-t border-slate-800 pt-4">
          <h3 className="text-sm text-slate-300">Create new VPP</h3>
          <input
            placeholder="name"
            required
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="w-full rounded border border-slate-700 bg-slate-800 px-3 py-1.5 text-white text-sm"
          />
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-slate-400">
              PV peak (kW)
              <input
                type="number"
                step="0.5"
                value={pvKw}
                onChange={(e) => setPvKw(Number(e.target.value))}
                className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-white text-sm"
              />
            </label>
            <label className="text-xs text-slate-400">
              Battery (kWh)
              <input
                type="number"
                step="1"
                value={battKwh}
                onChange={(e) => setBattKwh(Number(e.target.value))}
                className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-white text-sm"
              />
            </label>
          </div>
          <button
            type="button"
            onClick={() => setAdvancedOpen(!advancedOpen)}
            className="block text-xs text-sky-400 hover:text-sky-300"
          >
            {advancedOpen ? "Hide" : "Show"} advanced (real PV physics)
          </button>
          {advancedOpen && (
            <div className="space-y-2 rounded border border-slate-800 bg-slate-950/40 p-3">
              <p className="text-xs text-slate-400">
                Optional. Fill lat + lon to drive PV output from Open-Meteo weather data
                via pvlib (instead of the diurnal sine stub). Defaults to HKU rooftop.
              </p>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-slate-400">
                  Latitude
                  <input
                    type="number"
                    step="0.01"
                    placeholder="22.28"
                    value={pvLat}
                    onChange={(e) => setPvLat(e.target.value)}
                    className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-white text-sm"
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Longitude
                  <input
                    type="number"
                    step="0.01"
                    placeholder="114.13"
                    value={pvLon}
                    onChange={(e) => setPvLon(e.target.value)}
                    className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-white text-sm"
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Tilt (°)
                  <input
                    type="number"
                    step="1"
                    value={pvTilt}
                    onChange={(e) => setPvTilt(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-white text-sm"
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Azimuth (° from N)
                  <input
                    type="number"
                    step="5"
                    value={pvAzimuth}
                    onChange={(e) => setPvAzimuth(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-white text-sm"
                  />
                </label>
              </div>
            </div>
          )}
          <button
            disabled={busy}
            className="rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-50 px-4 py-1.5 text-white text-sm"
          >
            {busy ? "Creating…" : "Create"}
          </button>
        </form>
      </section>

      <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <h2 className="text-sm uppercase tracking-wide text-slate-400 mb-3">Submit order</h2>
        {vpps.length === 0 ? (
          <p className="text-slate-500">Manual orders require an external VPP.</p>
        ) : (
          <form onSubmit={onOrder} className="space-y-3">
            <label className="block text-xs text-slate-400">
              VPP
              <select
                value={orderVpp ?? ""}
                onChange={(e) => setOrderVpp(Number(e.target.value))}
                className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1.5 text-white text-sm"
              >
                {vpps.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} (#{v.id})
                  </option>
                ))}
              </select>
            </label>
            <div className="grid grid-cols-3 gap-2">
              <label className="text-xs text-slate-400">
                Side
                <select
                  value={side}
                  onChange={(e) => setSide(e.target.value as "buy" | "sell")}
                  className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1.5 text-white text-sm"
                >
                  <option value="buy">buy</option>
                  <option value="sell">sell</option>
                </select>
              </label>
              <label className="text-xs text-slate-400">
                Price
                <input
                  type="number"
                  step="0.01"
                  value={price}
                  onChange={(e) => setPrice(Number(e.target.value))}
                  className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-white text-sm"
                />
              </label>
              <label className="text-xs text-slate-400">
                Qty (kWh)
                <input
                  type="number"
                  step="0.01"
                  value={qty}
                  onChange={(e) => setQty(Number(e.target.value))}
                  className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 text-white text-sm"
                />
              </label>
            </div>
            <button
              disabled={busy}
              className={`rounded ${side === "buy" ? "bg-emerald-600 hover:bg-emerald-500" : "bg-rose-600 hover:bg-rose-500"} disabled:opacity-50 px-4 py-1.5 text-white text-sm`}
            >
              {busy ? "Submitting…" : `Submit ${side.toUpperCase()}`}
            </button>
            {lastOrder && <p className="text-xs text-emerald-300 mt-2">{lastOrder}</p>}
          </form>
        )}
      </section>

      {error && (
        <div className="col-span-full text-sm text-rose-400 border border-rose-900 bg-rose-950/40 rounded p-3">
          {error}
        </div>
      )}
    </div>
  );
}

function LLMBadge({ state }: { state: string }) {
  const styles: Record<string, string> = {
    live: "text-emerald-300",
    degraded: "text-amber-300",
    offline: "text-slate-500",
  };
  const labels: Record<string, string> = {
    live: "LLM live",
    degraded: "LLM degraded",
    offline: "LLM offline",
  };
  return (
    <span className={`shrink-0 text-xs ${styles[state] ?? "text-slate-400"}`}>
      {labels[state] ?? state}
    </span>
  );
}

function ManagedPerformancePanel({ data }: { data?: ManagedVPPPerformance }) {
  const { nameOf } = useMarket();
  const pnl = Number(data?.pnl ?? 0);
  const pnlClass = pnl >= 0 ? "text-emerald-300" : "text-rose-300";

  return (
    <div className="mt-3 border-t border-sky-900/70 pt-3">
      {!data ? (
        <div className="text-xs text-slate-500">Loading performance…</div>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Metric label="PnL ($)" value={Number(data.pnl).toFixed(4)} valueClass={pnlClass} />
            <Metric label="Battery SOC" value={`${(data.soc_frac * 100).toFixed(1)}%`} />
            <Metric label="Bought (kWh)" value={data.cumulative_energy_bought_kwh.toFixed(4)} />
            <Metric label="Sold (kWh)" value={data.cumulative_energy_sold_kwh.toFixed(4)} />
          </div>
          <ReflectionTimeline data={data} />
          <div className="overflow-hidden rounded border border-slate-800">
            <table className="w-full text-xs">
              <thead className="bg-slate-950/80 text-slate-400">
                <tr>
                  <th className="px-2 py-2 text-left">Time</th>
                  <th className="px-2 py-2 text-left">Side</th>
                  <th className="px-2 py-2 text-right">Price ($/kWh)</th>
                  <th className="px-2 py-2 text-right">Qty (kWh)</th>
                  <th className="px-2 py-2 text-right">Cash ($)</th>
                  <th className="px-2 py-2 text-right">Counterparty</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_trades.map((t) => (
                  <tr key={`${t.trade_id}-${t.side}`} className="border-t border-slate-800">
                    <td className="px-2 py-1.5 text-slate-300 tabular-nums">
                      {new Date(t.wall_ts).toLocaleTimeString("en-GB", { hour12: false })}
                    </td>
                    <td className={t.side === "buy" ? "px-2 py-1.5 text-emerald-300" : "px-2 py-1.5 text-rose-300"}>
                      {t.side}
                    </td>
                    <td className="px-2 py-1.5 text-right text-slate-200 tabular-nums">{Number(t.price).toFixed(2)}</td>
                    <td className="px-2 py-1.5 text-right text-slate-200 tabular-nums">{Number(t.qty).toFixed(4)}</td>
                    <td className="px-2 py-1.5 text-right text-slate-200 tabular-nums">{Number(t.cash).toFixed(4)}</td>
                    <td className="px-2 py-1.5 text-right text-slate-400">{nameOf(t.counterparty_vpp_id)}</td>
                  </tr>
                ))}
                {data.recent_trades.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-2 py-3 text-center text-slate-500">
                      No trades yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, valueClass = "text-white" }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="rounded border border-slate-800 bg-slate-950/50 px-2 py-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
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
    return parts.join(" · ");
  }
  if (r.price_adjust !== null && r.price_adjust !== undefined && r.qty_scale !== null && r.qty_scale !== undefined) {
    return `price ${r.price_adjust >= 0 ? "+" : ""}${(r.price_adjust * 100).toFixed(1)}% · qty ×${r.qty_scale.toFixed(2)}`;
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
      <div className="mb-1 flex items-baseline justify-between">
        <h4 className="text-[11px] uppercase tracking-wide text-slate-500">Guidance timeline</h4>
        {health && (
          <span className="text-[11px] text-slate-500">
            {health.ok_count} ok / {health.fail_count} failed
            {health.last_ok_ts &&
              ` · last ok ${new Date(health.last_ok_ts).toLocaleTimeString("en-GB", { hour12: false })}`}
          </span>
        )}
      </div>
      <div className="max-h-56 space-y-1.5 overflow-auto rounded border border-slate-800 bg-slate-950/40 p-2">
        {reflections.length === 0 && (
          <p className="px-1 py-2 text-center text-xs text-slate-500">
            No guidance yet — the agent consults the LLM every ~minute.
          </p>
        )}
        {reflections.map((r) => (
          <div key={r.ts} className="rounded border border-slate-800/80 bg-slate-900/40 px-2 py-1.5">
            <div className="flex items-center gap-2 text-[11px]">
              <span className="text-slate-400 tabular-nums">
                {new Date(r.ts).toLocaleTimeString("en-GB", { hour12: false })}
              </span>
              {r.ok ? (
                <>
                  <span className="text-emerald-300">ok</span>
                  <span className="text-sky-300 tabular-nums">{guidanceSummary(r)}</span>
                </>
              ) : (
                <span className="text-rose-300">failed</span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-300">
              {r.ok ? guidanceText(r) : r.error}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
