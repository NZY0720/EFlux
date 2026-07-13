import { useCallback, useEffect, useState, type FormEvent } from "react";
import { AlertCircle, Bot, CheckCircle2, Layers3, ShoppingCart, Trash2, Zap } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { deleteManagedVPP, deleteVPP, fetchManagedVPPPerformance, fetchProducts, listManagedVPPs, listModels, listVPPs, submitOrder } from "../api/client";
import type { DeliveryProduct, ManagedVPP, ManagedVPPPerformance, OrderPurpose, TimeInForce, VPP } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill } from "../components/DashboardCard";
import PriceChart from "../components/PriceChart";
import { algorithmChipLabel, isLlmManaged, LLMBadge, ManagedControls, ManagedPerformancePanel, NumberField } from "./vpps/LegacyVppParts";
import { strategyLabel } from "../lib/categories";
import { useMarketMode } from "../state/marketMode";
import { useMarket } from "../state/marketStream";

export default function VppCockpit() {
  const { id } = useParams();
  const vppId = Number(id);
  const [managed, setManaged] = useState<ManagedVPP | null>(null);
  const [external, setExternal] = useState<VPP | null>(null);
  const [performance, setPerformance] = useState<ManagedVPPPerformance>();
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [vpps, agents] = await Promise.all([listVPPs(), listManagedVPPs()]);
      const agent = agents.find((item) => item.id === vppId) ?? null;
      setManaged(agent);
      setExternal(agent ? null : (vpps.find((item) => item.id === vppId) ?? null));
      if (agent) setPerformance(await fetchManagedVPPPerformance(agent.id));
    } catch (err) { setError((err as Error).message); } finally { setLoading(false); }
  }, [vppId]);
  useEffect(() => { if (Number.isInteger(vppId) && vppId > 0) void reload(); else setLoading(false); }, [vppId, reload]);
  useEffect(() => {
    const syncManagedVpp = (event: Event) => {
      const updatedId = (event as CustomEvent<{ id?: number }>).detail?.id;
      if (updatedId === vppId) void reload();
    };
    window.addEventListener("eflux:managed-vpp-updated", syncManagedVpp);
    return () => window.removeEventListener("eflux:managed-vpp-updated", syncManagedVpp);
  }, [vppId, reload]);
  useEffect(() => { listModels().then((result) => setModels(result.models)).catch(() => {}); }, []);
  useEffect(() => {
    if (!managed) return;
    const timer = window.setInterval(() => fetchManagedVPPPerformance(managed.id).then(setPerformance).catch((err: Error) => setError(err.message)), 2000);
    return () => window.clearInterval(timer);
  }, [managed]);

  const removeManaged = async () => {
    if (!managed) return;
    try { await deleteManagedVPP(managed.id); window.location.assign("/vpps"); } catch (err) { setError((err as Error).message); }
  };
  const removeExternal = async () => {
    if (!external || !window.confirm("Delete this VPP? Its resting orders are cancelled.")) return;
    try { await deleteVPP(external.id); window.location.assign("/vpps"); } catch (err) { setError((err as Error).message); }
  };

  if (loading) return <div className="mx-auto w-full max-w-[1800px] px-4 py-5 text-sm text-[var(--text-muted)] md:p-6">Loading VPP…</div>;
  if (!managed && !external) return <div className="mx-auto w-full max-w-2xl px-4 py-12 text-center md:p-6"><EmptyState icon={Layers3} title="VPP not found" body="This VPP may have been deleted or is not available to your account." /><Link to="/vpps" className="eflux-btn eflux-btn-primary mt-4 h-9 px-4 text-sm">Back to VPPs</Link></div>;

  return <div className="mx-auto grid w-full max-w-[1800px] grid-cols-1 gap-6 px-4 py-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)] md:p-6">
    {managed ? <>
      <DashboardCard className="lg:col-span-2"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><Bot size={18} className="text-[var(--accent)]" /><h1 className="text-xl font-semibold text-[var(--text)]">{managed.name}</h1><StatusPill tone="accent">{algorithmChipLabel(managed)}</StatusPill>{isLlmManaged(managed) && <LLMBadge state={managed.llm_health_state} />}</div><p className="mt-2 text-sm text-[var(--text-muted)]">PV {managed.params.pv_kw_peak}kW / Batt {managed.params.battery_kwh}kWh / Load {managed.params.load_kw_base}kW</p><p className="mt-1 text-xs text-[var(--accent)]">{strategyLabel(managed.strategy)}</p>{isLlmManaged(managed) && <p className="mt-1 text-xs text-[var(--text-subtle)]">{managed.llm_status}</p>}</div><Link to={`/competitions/season-0/submit?vpp=${managed.id}`} className="eflux-btn eflux-btn-primary h-9 px-4 text-sm">Submit to Season 0</Link></div></DashboardCard>
      <DashboardCard className="lg:col-span-2"><CardTitle icon={Bot}>Performance & activity</CardTitle><ManagedPerformancePanel data={performance} /></DashboardCard>
      <VppActivity id={managed.vpp_id} name={managed.name} />
      <DashboardCard><CardTitle icon={Layers3}>Strategy & risk</CardTitle><p className="text-sm text-[var(--text-muted)]">{managed.persona || "No custom strategy brief. The selected algorithm uses platform defaults."}</p><dl className="mt-4 space-y-2 text-xs"><div className="flex justify-between gap-3"><dt className="text-[var(--text-subtle)]">Algorithm</dt><dd className="text-[var(--text)]">{algorithmChipLabel(managed)}</dd></div><div className="flex justify-between gap-3"><dt className="text-[var(--text-subtle)]">Guidance</dt><dd className="text-[var(--text)]">{managed.guidance_source ?? "platform"}</dd></div><div className="flex justify-between gap-3"><dt className="text-[var(--text-subtle)]">Risk</dt><dd className="text-[var(--text)]">Platform risk gate enabled</dd></div></dl></DashboardCard>
      <DashboardCard><CardTitle icon={Bot}>Agent controls</CardTitle><ManagedControls vpp={managed} models={models} onSaved={reload} onDelete={removeManaged} onError={setError} /></DashboardCard>
    </> : <>
      <DashboardCard className="lg:col-span-2"><div className="flex items-start justify-between gap-3"><div><h1 className="text-xl font-semibold text-[var(--text)]">{external!.name}</h1><p className="mt-2 text-sm text-[var(--text-muted)]">PV {external!.params.pv_kw_peak}kW / Batt {external!.params.battery_kwh}kWh / Load {external!.params.load_kw_base}kW</p></div><StatusPill tone={external!.is_active ? "success" : "danger"}>{external!.is_active ? "active" : "inactive"}</StatusPill></div></DashboardCard>
      <VppActivity id={external!.id} name={external!.name} />
      <DashboardCard><CardTitle icon={Layers3}>VPP status</CardTitle><p className="text-sm text-[var(--text-muted)]">This VPP is manually controlled. Submit orders from the panel alongside it.</p><button type="button" onClick={removeExternal} className="eflux-btn eflux-btn-danger mt-4 h-8 px-3 text-xs"><Trash2 size={14} /> Delete VPP</button></DashboardCard>
      <ManualOrder vpp={external!} onError={setError} />
    </>}
    {error && <div className="lg:col-span-2 flex items-start gap-2 rounded-lg bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]"><AlertCircle size={17} className="mt-0.5 shrink-0" />{error}</div>}
  </div>;
}

function VppActivity({ id, name }: { id: number; name: string }) {
  const { recent, snapshot } = useMarket();
  const { mode } = useMarketMode();
  const quote = snapshot?.external_market;
  const gridQuote = mode === "realprice" && quote && (quote.status === "real" || quote.status === "fallback") ? quote : null;
  return <DashboardCard className="lg:col-span-2"><CardTitle icon={Bot}>Trading activity</CardTitle><PriceChart variant={mode === "realprice" ? "realprice" : "p2p"} events={recent} myAgents={[{ id, name, color: "#059669" }]} hiddenAgentIds={[]} initialPrice={snapshot?.last_price ? Number(snapshot.last_price) : null} initialExternalPrice={gridQuote ? Number(gridQuote.raw_lmp) : null} initialImportPrice={gridQuote ? Number(gridQuote.import_price) : null} initialExportPrice={gridQuote ? Number(gridQuote.export_price) : null} /><p className="mt-2 text-xs text-[var(--text-muted)]">▲ buys ▼ sells · fills at the same timestamp are shown at their average price</p></DashboardCard>;
}

function ManualOrder({ vpp, onError }: { vpp: VPP; onError: (message: string | null) => void }) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [price, setPrice] = useState(50);
  const [qty, setQty] = useState(0.05);
  const [products, setProducts] = useState<DeliveryProduct[]>([]);
  const [productId, setProductId] = useState("");
  const [purpose, setPurpose] = useState<OrderPurpose>("balance");
  const [timeInForce, setTimeInForce] = useState<TimeInForce>("good_til_gate");
  const [ttlSec, setTtlSec] = useState(120);
  const [busy, setBusy] = useState(false);
  const [lastOrder, setLastOrder] = useState<string | null>(null);
  useEffect(() => {
    fetchProducts().then((rows) => {
      const open = rows.filter((row) => row.is_open && !row.is_closed);
      setProducts(open);
      setProductId((current) => current || open[0]?.product_id || "");
    }).catch((err: Error) => onError(err.message));
  }, [onError]);
  useEffect(() => {
    if (purpose === "dispatchable" && side === "buy") setPurpose("balance");
    if (purpose === "flex_load" && side === "sell") setPurpose("balance");
  }, [side, purpose]);
  const purposes: Array<{ value: OrderPurpose; label: string }> = side === "buy"
    ? [{ value: "balance", label: "Load balance" }, { value: "battery", label: "Battery charge" }, { value: "flex_load", label: "Flexible load" }]
    : [{ value: "balance", label: "Renewable surplus" }, { value: "battery", label: "Battery discharge" }, { value: "dispatchable", label: "Dispatchable generation" }];
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); onError(null); setLastOrder(null);
    try {
      const result = await submitOrder({ vpp_id: vpp.id, side, price, qty_kwh: qty, product_id: productId, purpose, time_in_force: timeInForce, ...(timeInForce === "good_til_gate" ? { ttl_sec: ttlSec } : {}) });
      setLastOrder(`order ${result.order_id} · ${result.product_id} · ${result.trades.length} fill(s), remaining=${result.remaining_qty} kWh`);
    } catch (err) { onError((err as Error).message); } finally { setBusy(false); }
  };
  return <DashboardCard><CardTitle icon={ShoppingCart}>Submit delivery order</CardTitle><form onSubmit={submit} className="space-y-3"><p className="text-xs text-[var(--text-muted)]">Submitting as {vpp.name} (#{vpp.id}). Prices are USD/MWh; negative prices are valid.</p><div className="inline-flex overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]">{(["buy", "sell"] as const).map((value) => <button key={value} type="button" onClick={() => setSide(value)} className={`px-4 py-2 text-sm font-semibold uppercase ${side === value ? value === "buy" ? "bg-[var(--success)] text-[var(--text-inverse)]" : "bg-[var(--danger)] text-[var(--text-inverse)]" : "text-[var(--text-muted)]"}`}>{value}</button>)}</div><label className="block text-xs font-medium text-[var(--text-muted)]">Delivery product<select required value={productId} onChange={(event) => setProductId(event.target.value)} className="eflux-select mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"><option value="" disabled>No open product</option>{products.map((product) => <option key={product.product_id} value={product.product_id}>{new Date(product.delivery_start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–{new Date(product.delivery_end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · gate {new Date(product.gate_closure).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</option>)}</select></label><div className="grid grid-cols-2 gap-2"><label className="block text-xs font-medium text-[var(--text-muted)]">Physical purpose<select value={purpose} onChange={(event) => setPurpose(event.target.value as OrderPurpose)} className="eflux-select mt-1 w-full rounded-md px-3 py-2 text-sm outline-none">{purposes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label className="block text-xs font-medium text-[var(--text-muted)]">Time in force<select value={timeInForce} onChange={(event) => setTimeInForce(event.target.value as TimeInForce)} className="eflux-select mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"><option value="good_til_gate">Good til gate</option><option value="immediate_or_cancel">IOC</option><option value="fill_or_kill">FOK</option></select></label></div><div className="grid grid-cols-2 gap-2"><NumberField label="Price (USD/MWh)" value={price} step="0.01" onChange={setPrice} /><NumberField label="Qty (kWh)" value={qty} step="0.01" onChange={setQty} /></div>{timeInForce === "good_til_gate" && <NumberField label="TTL (sim seconds)" value={ttlSec} step="1" onChange={setTtlSec} />}<p className="text-[11px] text-[var(--text-subtle)]">Purpose is a physical commitment: the gateway checks battery power/SOC, generation ramp, flexible-load headroom, and delivery energy before accepting.</p><button disabled={busy || !productId} className={`eflux-btn h-10 w-full px-4 text-sm font-semibold disabled:opacity-50 ${side === "buy" ? "eflux-btn-success" : "eflux-btn-danger"}`}><Zap size={16} />{busy ? "Submitting…" : `Submit ${side.toUpperCase()}`}</button>{lastOrder && <div className="flex items-center gap-2 rounded-lg bg-[var(--success-soft)] px-3 py-2 text-sm text-[var(--success)]"><CheckCircle2 size={16} />{lastOrder}</div>}</form></DashboardCard>;
}
