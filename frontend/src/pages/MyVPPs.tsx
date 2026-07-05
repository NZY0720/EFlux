import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertCircle,
  BatteryCharging,
  Bot,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Copy,
  KeyRound,
  Layers3,
  LineChart,
  ListChecks,
  MapPinned,
  MessagesSquare,
  PlusCircle,
  Save,
  Settings2,
  ShoppingCart,
  Terminal,
  Trash2,
  Zap,
} from "lucide-react";

import {
  type ApiKeyInfo,
  createManagedVPP,
  createVPP,
  deleteManagedVPP,
  deleteVPP,
  fetchManagedVPPPerformance,
  listAlgorithms,
  listApiKeys,
  listManagedVPPs,
  listModels,
  listVPPs,
  mintApiKey,
  releaseGuidance,
  revokeApiKey,
  sayInChatroom,
  setChatPrefs,
  submitOrder,
  updateManagedVPP,
} from "../api/client";

// Chatroom name colors an owner can pick (mirrors the room's auto-badge palette).
const CHAT_COLORS = ["#059669", "#0284c7", "#7c3aed", "#d97706", "#e11d48", "#0d9488", "#9333ea", "#0891b2"];

const LOAD_PROFILES = ["residential", "industrial", "commercial", "flat"];
import type { AlgorithmInfo, AlgorithmParam, ManagedVPP, ManagedVPPPerformance, ReflectionEntry, VPP } from "../api/types";
import { CardTitle, DashboardCard, EmptyState, StatusPill, TableShell } from "../components/DashboardCard";
import PriceChart from "../components/PriceChart";
import { strategyLabel } from "../lib/categories";
import { useMarketMode } from "../state/marketMode";
import { useMarket } from "../state/marketStream";

type AlgorithmParamValue = number | string | boolean;

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
  const [newLoad, setNewLoad] = useState(2);
  const [newWind, setNewWind] = useState(0);
  const [newLoadProfile, setNewLoadProfile] = useState("residential");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [pvLat, setPvLat] = useState<string>("");
  const [pvLon, setPvLon] = useState<string>("");
  const [pvTilt, setPvTilt] = useState(30);
  const [pvAzimuth, setPvAzimuth] = useState(180);

  // Managed (Tier 0) agent creation.
  const [mgName, setMgName] = useState("");
  const [mgPv, setMgPv] = useState(4);
  const [mgBatt, setMgBatt] = useState(10);
  const [mgLoad, setMgLoad] = useState(2);
  const [mgPersona, setMgPersona] = useState("");
  const [mgDemandBeta, setMgDemandBeta] = useState(0.5);
  const [mgModel, setMgModel] = useState("");
  const [mgWind, setMgWind] = useState(0);
  const [mgLoadProfile, setMgLoadProfile] = useState("residential");
  const [mgAlgorithm, setMgAlgorithm] = useState("ppo");
  const [mgLlmEnabled, setMgLlmEnabled] = useState(true);
  const [mgOnlineLearning, setMgOnlineLearning] = useState(true);
  const [mgAdvancedOpen, setMgAdvancedOpen] = useState(false);
  const [mgAdvancedParams, setMgAdvancedParams] = useState<Record<string, AlgorithmParamValue>>({});
  const [models, setModels] = useState<string[]>([]);
  const [algorithms, setAlgorithms] = useState<AlgorithmInfo[]>([]);
  const [mgBusy, setMgBusy] = useState(false);

  const [orderVpp, setOrderVpp] = useState<number | null>(null);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [price, setPrice] = useState(50);
  const [qty, setQty] = useState(0.05);
  const [lastOrder, setLastOrder] = useState<string | null>(null);
  const [hiddenAgentIds, setHiddenAgentIds] = useState<Set<number>>(() => new Set());
  const { recent, snapshot } = useMarket();
  const { mode: marketMode } = useMarketMode();

  const myAgents = useMemo(() => {
    let idx = 0;
    return [
      ...managedVpps.map((v) => ({ id: v.vpp_id, name: v.name, color: CHAT_COLORS[idx++ % CHAT_COLORS.length] })),
      ...vpps.map((v) => ({ id: v.id, name: v.name, color: CHAT_COLORS[idx++ % CHAT_COLORS.length] })),
    ];
  }, [vpps, managedVpps]);
  const hiddenAgentIdList = useMemo(() => [...hiddenAgentIds], [hiddenAgentIds]);
  const toggleAgent = (id: number) => {
    setHiddenAgentIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

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
    listModels()
      .then((m) => {
        setModels(m.models);
        setMgModel((cur) => cur || m.default);
      })
      .catch(() => {});
    listAlgorithms()
      .then((roster) => {
        setAlgorithms(roster);
        setMgAlgorithm((cur) => (roster.some((a) => a.id === cur) ? cur : (roster[0]?.id ?? "ppo")));
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedAlgorithm =
    algorithms.find((a) => a.id === mgAlgorithm) ??
    ({
      id: "ppo",
      label: "PPO",
      description: "Structured-policy tactical executor over the shared action space.",
      llm_capable: true,
      supports_online_learning: true,
      params: [{ name: "demand_beta", type: "float", default: 0.5, min: 0, max: 1, help: "Demand response sensitivity." }],
    } satisfies AlgorithmInfo);
  const supportsDemandBeta = selectedAlgorithm.params.some((p) => p.name === "demand_beta");
  const isPpoAlgorithm = selectedAlgorithm.id === "ppo";
  const advancedParams = selectedAlgorithm.params.filter((p) => p.name !== "demand_beta");
  const setAlgorithm = (id: string) => {
    setMgAlgorithm(id);
    setMgAdvancedParams({});
    setMgAdvancedOpen(false);
  };

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
      const params: Record<string, number | string> = {
        pv_kw_peak: pvKw,
        battery_kwh: battKwh,
        load_kw_base: newLoad,
        load_profile: newLoadProfile,
      };
      if (newWind > 0) params.wind_kw_rated = newWind;
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

  const onCreateManaged = async (e: FormEvent) => {
    e.preventDefault();
    setMgBusy(true);
    setError(null);
    try {
      const params: Record<string, number | string> = {
        pv_kw_peak: mgPv,
        battery_kwh: mgBatt,
        load_kw_base: mgLoad,
        load_profile: mgLoadProfile,
      };
      if (mgWind > 0) params.wind_kw_rated = mgWind;
      const agentParams: Record<string, AlgorithmParamValue> = {
        ...mgAdvancedParams,
        ...(supportsDemandBeta ? { demand_beta: mgDemandBeta } : {}),
      };
      const created = await createManagedVPP({
        name: mgName.trim(),
        params,
        algorithm: selectedAlgorithm.id,
        llm_enabled: mgLlmEnabled,
        ...(isPpoAlgorithm ? { online_learning: mgOnlineLearning } : {}),
        ...(Object.keys(agentParams).length > 0 ? { agent_params: agentParams } : {}),
        ...(mgLlmEnabled
          ? {
              persona: mgPersona.trim() || null,
              model: mgModel || null,
            }
          : {}),
      });
      setMgName("");
      setMgPersona("");
      await reload();
      setSelectedManaged(created.id);
      await loadPerformance(created.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setMgBusy(false);
    }
  };

  const onDeleteManaged = async (id: number) => {
    setError(null);
    try {
      await deleteManagedVPP(id);
      if (selectedManaged === id) setSelectedManaged(null);
      await reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const onDeleteVPP = async (id: number) => {
    if (!window.confirm("Delete this VPP? Its resting orders are cancelled.")) return;
    setError(null);
    try {
      await deleteVPP(id);
      if (orderVpp === id) setOrderVpp(null);
      await reload();
    } catch (err) {
      setError((err as Error).message);
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
      <DashboardCard className="lg:col-span-2">
        <CardTitle icon={Bot}>Deploy a cloud-hosted agent</CardTitle>
        <p className="mb-4 text-sm text-[var(--text-muted)]">
          We run a trading agent for you — no code, no infrastructure. Pick a DER endowment and a base
          algorithm (PPO or a classical baseline), then optionally layer an LLM strategist on top to
          coach it. Every order passes the platform risk gate. Tune or remove it anytime.
        </p>
        <form onSubmit={onCreateManaged} className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-3">
            <input
              placeholder="agent name"
              required
              value={mgName}
              onChange={(e) => setMgName(e.target.value)}
              className="eflux-input w-full rounded-md px-3 py-2 text-sm outline-none"
            />
            <SelectField
              label="Algorithm"
              value={mgAlgorithm}
              options={algorithms.map((a) => ({ value: a.id, label: a.label }))}
              onChange={setAlgorithm}
            />
            <p className="text-xs text-[var(--text-muted)]">{selectedAlgorithm.description}</p>
            <label className="flex items-center gap-2 text-xs font-medium text-[var(--text)]">
              <input
                type="checkbox"
                checked={mgLlmEnabled}
                onChange={(e) => setMgLlmEnabled(e.target.checked)}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              Enable LLM strategist
              <StatusPill tone="accent" className="py-0 text-[10px]">
                {mgLlmEnabled ? `LLM + ${selectedAlgorithm.label}` : selectedAlgorithm.label}
              </StatusPill>
            </label>
            <p className="text-[11px] text-[var(--text-subtle)]">
              {mgLlmEnabled
                ? "A slow LLM strategist coaches the base executor (mode bias, risk & price nudges); the base algorithm still drives quoting."
                : "Runs the base algorithm on its own — no LLM, zero platform model cost."}
            </p>
            {mgLlmEnabled && (
              <SelectField label="LLM model" value={mgModel} options={models} onChange={setMgModel} />
            )}
            <div className="grid grid-cols-2 gap-2">
              <NumberField label="PV peak (kW)" value={mgPv} step="0.5" onChange={setMgPv} />
              <NumberField label="Battery (kWh)" value={mgBatt} step="1" onChange={setMgBatt} />
              <NumberField label="Load (kW)" value={mgLoad} step="0.5" onChange={setMgLoad} />
              <NumberField label="Wind (kW)" value={mgWind} step="0.5" onChange={setMgWind} />
            </div>
            <SelectField
              label="Load profile"
              value={mgLoadProfile}
              options={LOAD_PROFILES}
              onChange={setMgLoadProfile}
            />
            {isPpoAlgorithm && (
              <label className="flex items-center gap-2 text-xs font-medium text-[var(--text-muted)]">
                <input
                  type="checkbox"
                  checked={mgOnlineLearning}
                  onChange={(e) => setMgOnlineLearning(e.target.checked)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                Online learning
              </label>
            )}
            {supportsDemandBeta && (
              <SliderField
                label={`Demand response (demand_beta ${mgDemandBeta.toFixed(2)})`}
                value={mgDemandBeta}
                min={0}
                max={1}
                step={0.05}
                onChange={setMgDemandBeta}
              />
            )}
            {!isPpoAlgorithm && advancedParams.length > 0 && (
              <>
                <button
                  type="button"
                  onClick={() => setMgAdvancedOpen((open) => !open)}
                  className="eflux-btn h-8 px-3 text-xs"
                >
                  <Settings2 size={14} />
                  {mgAdvancedOpen ? "Hide algorithm parameters" : "Algorithm parameters (advanced)"}
                </button>
                {mgAdvancedOpen && (
                  <div className="eflux-inset grid grid-cols-1 gap-2 rounded-lg p-3 sm:grid-cols-2">
                    {advancedParams.map((param) => (
                      <AlgorithmParamField
                        key={param.name}
                        param={param}
                        value={mgAdvancedParams[param.name]}
                        onChange={(value) =>
                          setMgAdvancedParams((prev) => ({
                            ...prev,
                            [param.name]: value,
                          }))
                        }
                      />
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
          <div className="flex flex-col">
            {mgLlmEnabled && (
              <label className="flex flex-1 flex-col text-xs font-medium text-[var(--text-muted)]">
                Strategy brief (persona) — optional
                <textarea
                  value={mgPersona}
                  onChange={(e) => setMgPersona(e.target.value)}
                  rows={4}
                  maxLength={600}
                  placeholder="e.g. Prefer maker orders and capture spreads; avoid crossing unless imbalance is urgent. Hold SOC near 55%."
                  className="eflux-input mt-1 min-h-[96px] flex-1 resize-none rounded-md px-3 py-2 text-sm outline-none"
                />
              </label>
            )}
            <button
              disabled={mgBusy}
              className="eflux-btn eflux-btn-primary mt-3 h-9 px-4 text-sm font-semibold disabled:opacity-50 md:mt-auto"
            >
              <Bot size={15} />
              {mgBusy ? "Deploying..." : "Deploy agent"}
            </button>
          </div>
        </form>
      </DashboardCard>

      <DashboardCard className="lg:col-span-2">
        <CardTitle icon={LineChart}>My trading activity</CardTitle>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          {myAgents.map((agent) => {
            const hidden = hiddenAgentIds.has(agent.id);
            return (
              <button
                key={agent.id}
                type="button"
                onClick={() => toggleAgent(agent.id)}
                className={`inline-flex h-7 max-w-[220px] items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors ${
                  hidden
                    ? "border-[var(--border)] text-[var(--text-subtle)] opacity-55 line-through hover:bg-[var(--surface-hover)]"
                    : "border-[var(--border)] text-[var(--text)] hover:bg-[var(--surface-hover)]"
                }`}
              >
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: agent.color }} />
                <span className="truncate">{agent.name}</span>
              </button>
            );
          })}
          {hiddenAgentIds.size > 0 && (
            <button
              type="button"
              onClick={() => setHiddenAgentIds(new Set())}
              className="inline-flex h-7 items-center rounded-md border border-[var(--border)] px-2.5 text-xs font-medium text-[var(--accent)] transition-colors hover:bg-[var(--surface-hover)]"
            >
              Show all
            </button>
          )}
        </div>
        <PriceChart
          variant={marketMode === "realprice" ? "realprice" : "p2p"}
          events={recent}
          myAgents={myAgents}
          hiddenAgentIds={hiddenAgentIdList}
          initialPrice={snapshot?.last_price ? Number(snapshot.last_price) : null}
          initialExternalPrice={
            marketMode === "realprice" &&
            snapshot?.external_market &&
            (snapshot.external_market.status === "real" || snapshot.external_market.status === "fallback") &&
            snapshot.external_market.raw_lmp
              ? Number(snapshot.external_market.raw_lmp)
              : null
          }
        />
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          ▲ buys ▼ sells · color = agent · fills at the same timestamp are shown at their average price
        </p>
      </DashboardCard>

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
                      <StatusPill tone="accent" className="py-0 text-[10px]">{algorithmChipLabel(v)}</StatusPill>
                      {isLlmManaged(v) && v.model && (
                        <StatusPill tone="muted" className="py-0 text-[10px]">{v.model}</StatusPill>
                      )}
                      {isLlmManaged(v) && v.guidance_source === "external" && (
                        <StatusPill tone="violet" className="py-0 text-[10px]">externally steered</StatusPill>
                      )}
                    </div>
                    <div className="mt-1 text-xs text-[var(--text-muted)]">
                      PV {v.params.pv_kw_peak}kW / Batt {v.params.battery_kwh}kWh / Load {v.params.load_kw_base}kW
                    </div>
                    <div className="mt-1 text-xs text-[var(--accent)]">{strategyLabel(v.strategy)}</div>
                    {isLlmManaged(v) && <div className="mt-1 text-xs text-[var(--text-subtle)]">{v.llm_status}</div>}
                  </div>
                  {isLlmManaged(v) && <LLMBadge state={v.llm_health_state} />}
                </button>
                {selectedManaged === v.id && (
                  <>
                    <ManagedPerformancePanel data={performance[v.id]} />
                    <ManagedControls
                      vpp={v}
                      models={models}
                      onSaved={async () => {
                        await reload();
                        await loadPerformance(v.id);
                      }}
                      onDelete={() => onDeleteManaged(v.id)}
                      onError={setError}
                    />
                  </>
                )}
              </li>
            ))}

            {vpps.map((v) => (
              <li key={v.id} className="eflux-inset rounded-lg px-3 py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <div className="min-w-0">
                    <span className="font-medium text-[var(--text)]">{v.name}</span>
                    <span className="ml-2 text-xs text-[var(--text-subtle)]">#{v.id}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusPill tone={v.is_active ? "success" : "danger"}>{v.is_active ? "active" : "inactive"}</StatusPill>
                    {v.is_active && (
                      <button
                        type="button"
                        onClick={() => onDeleteVPP(v.id)}
                        aria-label={`delete ${v.name}`}
                        title="Delete this VPP"
                        className="eflux-btn eflux-btn-danger h-7 px-2 text-xs"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                </div>
                <div className="mt-1 text-xs text-[var(--text-muted)]">
                  PV {v.params.pv_kw_peak}kW / Batt {v.params.battery_kwh}kWh / Load {v.params.load_kw_base}kW
                  {v.params.wind_kw_rated ? ` / Wind ${v.params.wind_kw_rated}kW` : ""}
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
            <NumberField label="Load (kW)" value={newLoad} step="0.5" onChange={setNewLoad} />
            <NumberField label="Wind (kW)" value={newWind} step="0.5" onChange={setNewWind} />
          </div>
          <SelectField
            label="Load profile"
            value={newLoadProfile}
            options={LOAD_PROFILES}
            onChange={setNewLoadProfile}
          />
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

      <ApiAutomationCard vpps={vpps} onError={setError} />

      {error && (
        <div className="lg:col-span-2 flex items-start gap-2 rounded-lg border border-[color-mix(in_srgb,var(--danger)_42%,transparent)] bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]">
          <AlertCircle size={17} className="mt-0.5 shrink-0" />
          {error}
        </div>
      )}
    </div>
  );
}

function ManagedControls({
  vpp,
  models,
  onSaved,
  onDelete,
  onError,
}: {
  vpp: ManagedVPP;
  models: string[];
  onSaved: () => Promise<void> | void;
  onDelete: () => void;
  onError: (msg: string | null) => void;
}) {
  const hybrid = isLlmManaged(vpp);
  const [persona, setPersona] = useState(vpp.persona ?? "");
  const [model, setModel] = useState(vpp.model ?? "");
  const [demandBeta, setDemandBeta] = useState(0.5);
  const [betaDirty, setBetaDirty] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // Chatroom presence: speak as the agent + voice/color/avatar prefs.
  const [chatOpen, setChatOpen] = useState(false);
  const [sayText, setSayText] = useState("");
  const [said, setSaid] = useState<string | null>(null);
  const [chatStyle, setChatStyle] = useState(vpp.chat_style ?? "");
  const [chatColor, setChatColor] = useState(vpp.chat_color ?? "");
  const [chatAvatar, setChatAvatar] = useState(vpp.chat_avatar ?? "");

  const onSave = async () => {
    setBusy(true);
    onError(null);
    try {
      await updateManagedVPP(vpp.id, {
        persona: persona.trim(),
        ...(model ? { model } : {}),
        // Only send agent_params if the slider was touched, so a persona-only edit
        // doesn't silently reset demand_beta to the form default.
        ...(betaDirty ? { agent_params: { demand_beta: demandBeta } } : {}),
      });
      await onSaved();
      setOpen(false);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onReleaseGuidance = async () => {
    setBusy(true);
    onError(null);
    try {
      await releaseGuidance(vpp.id);
      await onSaved();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onSay = async (e: FormEvent) => {
    e.preventDefault();
    if (!sayText.trim()) return;
    setBusy(true);
    onError(null);
    try {
      await sayInChatroom(vpp.id, sayText.trim());
      setSaid(sayText.trim());
      setSayText("");
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onSaveChatPrefs = async () => {
    setBusy(true);
    onError(null);
    try {
      await setChatPrefs(vpp.id, {
        style: chatStyle.trim() || null,
        color: chatColor || null,
        avatar: chatAvatar.trim() || null,
      });
      await onSaved();
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] px-3 py-2">
      {hybrid && (
        <button type="button" onClick={() => setOpen((o) => !o)} className="eflux-btn h-8 px-3 text-xs">
          <Settings2 size={14} />
          {open ? "Close" : "Tune preferences"}
        </button>
      )}
      {hybrid && vpp.guidance_source === "external" && (
        <button
          type="button"
          onClick={onReleaseGuidance}
          disabled={busy}
          className="eflux-btn h-8 px-3 text-xs disabled:opacity-50"
          title="Your model is steering this agent (Tier A3). Hand strategy back to the platform LLM."
        >
          <BrainCircuit size={14} />
          Return to platform LLM
        </button>
      )}
      <button type="button" onClick={() => setChatOpen((o) => !o)} className="eflux-btn h-8 px-3 text-xs">
        <MessagesSquare size={14} />
        {chatOpen ? "Close chatroom" : "Chatroom"}
      </button>
      <button type="button" onClick={onDelete} className="eflux-btn eflux-btn-danger h-8 px-3 text-xs">
        <Trash2 size={14} />
        Delete
      </button>
      {chatOpen && (
        <div className="mt-2 w-full space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3">
          <form onSubmit={onSay} className="space-y-1.5">
            <label className="block text-xs font-medium text-[var(--text-muted)]" htmlFor={`say-${vpp.id}`}>
              Speak as {vpp.name} (posts publicly; the other agents can reply)
            </label>
            <div className="flex gap-2">
              <input
                id={`say-${vpp.id}`}
                value={sayText}
                onChange={(e) => setSayText(e.target.value)}
                maxLength={200}
                placeholder="say something in the room"
                className="eflux-input min-w-0 flex-1 rounded-md px-3 py-1.5 text-sm outline-none"
              />
              <button
                type="submit"
                disabled={busy || !sayText.trim()}
                className="eflux-btn eflux-btn-primary h-8 px-3 text-xs disabled:opacity-50"
              >
                Say it
              </button>
            </div>
            {said && (
              <p className="text-[11px] text-[var(--success)]">Posted: "{said}"</p>
            )}
          </form>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Chat voice (tone only; does not touch trading)
            <input
              value={chatStyle}
              onChange={(e) => setChatStyle(e.target.value)}
              maxLength={200}
              placeholder="e.g. dry one-liners, always quotes battery SOC"
              className="eflux-input mt-1 w-full rounded-md px-3 py-1.5 text-sm outline-none"
            />
          </label>
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <div className="text-xs font-medium text-[var(--text-muted)]">Name color</div>
              <div className="mt-1.5 flex items-center gap-1.5">
                {CHAT_COLORS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setChatColor(chatColor === c ? "" : c)}
                    aria-label={`use color ${c}`}
                    className={`h-6 w-6 rounded-full border-2 transition-transform hover:scale-110 ${
                      chatColor === c ? "border-[var(--text)]" : "border-transparent"
                    }`}
                    style={{ backgroundColor: c }}
                  />
                ))}
              </div>
            </div>
            <label className="block text-xs font-medium text-[var(--text-muted)]">
              Avatar (one emoji)
              <input
                value={chatAvatar}
                onChange={(e) => setChatAvatar(e.target.value)}
                maxLength={4}
                placeholder="none"
                className="eflux-input mt-1 w-20 rounded-md px-3 py-1.5 text-center text-sm outline-none"
              />
            </label>
            <button
              type="button"
              disabled={busy}
              onClick={onSaveChatPrefs}
              className="eflux-btn h-8 px-3 text-xs disabled:opacity-50"
            >
              <Save size={14} />
              Save presence
            </button>
          </div>
        </div>
      )}
      {hybrid && open && (
        <div className="mt-2 w-full space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3">
          <p className="text-[11px] text-[var(--text-subtle)]">
            Applying changes restarts the agent&apos;s trading session (open orders reset; PnL is kept).
          </p>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Strategy brief (persona)
            <textarea
              value={persona}
              onChange={(e) => setPersona(e.target.value)}
              rows={3}
              maxLength={600}
              className="eflux-input mt-1 w-full resize-none rounded-md px-3 py-2 text-sm outline-none"
            />
          </label>
          <SelectField label="LLM model" value={model} options={models} onChange={setModel} />
          <SliderField
            label={`Demand response (demand_beta ${demandBeta.toFixed(2)})`}
            value={demandBeta}
            min={0}
            max={1}
            step={0.05}
            onChange={(v) => {
              setDemandBeta(v);
              setBetaDirty(true);
            }}
          />
          <button
            type="button"
            disabled={busy}
            onClick={onSave}
            className="eflux-btn eflux-btn-primary h-8 px-3 text-xs disabled:opacity-50"
          >
            <Save size={14} />
            {busy ? "Saving..." : "Save & restart agent"}
          </button>
        </div>
      )}
    </div>
  );
}

function ApiAutomationCard({ vpps, onError }: { vpps: VPP[]; onError: (msg: string | null) => void }) {
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [keyName, setKeyName] = useState("");
  const [minted, setMinted] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [selectedVppId, setSelectedVppId] = useState<number | null>(null);

  const reloadKeys = async () => {
    try {
      setKeys(await listApiKeys());
    } catch (e) {
      onError((e as Error).message);
    }
  };
  useEffect(() => {
    reloadKeys();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onMint = async (e: FormEvent) => {
    e.preventDefault();
    if (!keyName.trim()) return;
    setBusy(true);
    onError(null);
    try {
      const k = await mintApiKey(keyName.trim());
      setMinted(k.key);
      setKeyName("");
      await reloadKeys();
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async (prefix: string) => {
    if (!window.confirm("Revoke this API key? Apps using it will stop working.")) return;
    onError(null);
    try {
      await revokeApiKey(prefix);
      await reloadKeys();
    } catch (err) {
      onError((err as Error).message);
    }
  };

  const activeKeys = keys.filter((k) => !k.revoked_at);
  // The VPP this snippet targets — the one the user picked, else their first VPP.
  const targetVpp = vpps.find((v) => v.id === selectedVppId) ?? vpps[0] ?? null;
  const targetId = targetVpp ? String(targetVpp.id) : "<your_vpp_id>";
  const snippet = `# pip install -e .   (then adapt the repo's examples/market_maker.py)
from eflux.sdk import EFluxClient

client = EFluxClient(base_url="http://localhost:8000", api_key="<YOUR_API_KEY>")

# Read the market, then submit a replay-safe batch for ${targetVpp ? `"${targetVpp.name}"` : "your VPP"}.
snap = await client.market_snapshot(depth=10)
await client.submit_batch(
    orders=[{"vpp_id": ${targetId}, "side": "buy", "price": 48.0, "qty": 0.2}],
    idempotency_key="quote-001",  # a resend returns the original result, never a double order
)`;

  const copySnippet = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — the user can select the text manually */
    }
  };

  return (
    <DashboardCard className="lg:col-span-2">
      <CardTitle icon={Terminal}>Automate your VPPs (external app)</CardTitle>
      <p className="mb-3 text-sm text-[var(--text-muted)]">
        First create a VPP under <span className="font-semibold text-[var(--text)]">My VPPs</span> above,
        then pick it here to drive it from your own local bot via the Agent Protocol. The platform
        provides the interface and policies; your decision logic stays on your machine.
      </p>

      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--text)]">
          <KeyRound size={15} className="text-[var(--accent)]" /> API keys
        </h3>
        <form onSubmit={onMint} className="flex flex-wrap gap-2">
          <input
            value={keyName}
            onChange={(e) => setKeyName(e.target.value)}
            placeholder="key name (e.g. market-maker-bot)"
            maxLength={100}
            className="eflux-input min-w-0 flex-1 rounded-md px-3 py-2 text-sm outline-none"
          />
          <button
            disabled={busy || !keyName.trim()}
            className="eflux-btn eflux-btn-primary h-9 px-4 text-sm font-semibold disabled:opacity-50"
          >
            <KeyRound size={14} /> Mint key
          </button>
        </form>
        {minted && (
          <div className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_45%,transparent)] bg-[var(--warning-soft)] p-3 text-xs">
            <div className="mb-1 font-semibold text-[var(--warning)]">
              Copy this key now — it is shown only once.
            </div>
            <code className="block break-all font-mono text-[var(--text)]">{minted}</code>
          </div>
        )}
        {activeKeys.length > 0 ? (
          <ul className="space-y-1">
            {activeKeys.map((k) => (
              <li
                key={k.prefix}
                className="flex items-center justify-between gap-2 rounded-md bg-[var(--surface-inset)] px-3 py-1.5 text-xs"
              >
                <span className="min-w-0 truncate">
                  <span className="font-medium text-[var(--text)]">{k.name}</span>
                  <span className="ml-2 font-mono text-[var(--text-subtle)]">{k.prefix}…</span>
                </span>
                <button
                  type="button"
                  onClick={() => onRevoke(k.prefix)}
                  className="eflux-btn eflux-btn-danger h-7 px-2 text-xs"
                >
                  <Trash2 size={12} /> Revoke
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-[var(--text-subtle)]">No active keys. Mint one to authenticate your bot.</p>
        )}
      </div>

      <div className="mt-4 space-y-1 text-xs text-[var(--text-muted)]">
        <div>
          <span className="font-semibold text-[var(--text)]">Endpoint</span> —{" "}
          <code className="font-mono">POST /orders/batch</code> (Agent Protocol v1:{" "}
          <code className="font-mono">idempotency_key</code>, <code className="font-mono">deadline</code>, cancels-first)
        </div>
        <div>
          <span className="font-semibold text-[var(--text)]">Auth</span> —{" "}
          <code className="font-mono">Authorization: Bearer &lt;API_KEY&gt;</code>
        </div>
        <div>
          <span className="font-semibold text-[var(--text)]">Rate limit</span> — 120 burst / 2·s⁻¹ per account
          (429 on exceed); every order still passes the RiskGate.
        </div>
      </div>

      {vpps.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-[var(--border)] bg-[var(--surface-inset)] p-3 text-xs text-[var(--text-muted)]">
          <span className="font-semibold text-[var(--text)]">No VPP to automate yet.</span> Create one
          under <span className="font-semibold">My VPPs</span> above, then pick it here to get a
          ready-to-run snippet that targets it.
        </div>
      ) : (
        <div className="mt-4 space-y-2">
          <SelectField
            label="Automate this VPP"
            value={targetId}
            options={vpps.map((v) => ({ value: String(v.id), label: `${v.name} (#${v.id})` }))}
            onChange={(val) => setSelectedVppId(Number(val))}
          />
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-[var(--text)]">Quick start</span>
            <button type="button" onClick={copySnippet} className="eflux-btn h-7 px-2 text-xs">
              <Copy size={12} /> {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <pre className="eflux-inset overflow-x-auto rounded-lg p-3 text-[11px] leading-relaxed text-[var(--text)]">
            <code>{snippet}</code>
          </pre>
          <p className="text-[11px] text-[var(--text-subtle)]">
            Full runnable example: <code className="font-mono">examples/market_maker.py</code> · SDK:{" "}
            <code className="font-mono">eflux.sdk.EFluxClient</code> · MCP server:{" "}
            <code className="font-mono">eflux.mcp.server</code>.
          </p>
        </div>
      )}
    </DashboardCard>
  );
}

function isLlmManaged(vpp: ManagedVPP): boolean {
  // The LLM strategist is layered on the base algorithm. Fall back to legacy signals for rows
  // provisioned before llm_enabled existed (old "hybrid" tag / HybridPolicyAgent strategy string).
  return (
    vpp.llm_enabled ||
    vpp.algorithm === "hybrid" ||
    (!vpp.algorithm && vpp.strategy.startsWith("HybridPolicyAgent"))
  );
}

const ALGORITHM_LABELS: Record<string, string> = {
  hybrid: "PPO", // legacy fused tag → base is PPO
  ppo: "PPO",
  truthful: "Truthful",
  zip: "ZIP",
  gd: "GD",
  aa: "AA",
};

function algorithmChipLabel(vpp: ManagedVPP): string {
  const base = ALGORITHM_LABELS[vpp.algorithm] ?? vpp.algorithm ?? "managed";
  return isLlmManaged(vpp) ? `LLM + ${base}` : base;
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<string | { value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="eflux-select mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
      >
        {options.map((o) => (
          <option key={typeof o === "string" ? o : o.value} value={typeof o === "string" ? o : o.value}>
            {typeof o === "string" ? o : o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function AlgorithmParamField({
  param,
  value,
  onChange,
}: {
  param: AlgorithmParam;
  value: AlgorithmParamValue | undefined;
  onChange: (value: AlgorithmParamValue) => void;
}) {
  const inputValue = value ?? param.default ?? (isNumericParam(param) ? 0 : "");
  if (isNumericParam(param)) {
    const numericValue = typeof inputValue === "number" ? inputValue : Number(inputValue);
    return (
      <NumberField
        label={param.help ? `${param.name} - ${param.help}` : param.name}
        value={Number.isFinite(numericValue) ? numericValue : 0}
        step={param.type === "int" || param.type === "integer" ? "1" : "0.01"}
        min={param.min ?? undefined}
        max={param.max ?? undefined}
        onChange={onChange}
      />
    );
  }
  if (param.type === "bool" || param.type === "boolean") {
    return (
      <label className="flex items-center gap-2 text-xs font-medium text-[var(--text-muted)]">
        <input
          type="checkbox"
          checked={Boolean(inputValue)}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 accent-[var(--accent)]"
        />
        {param.help ? `${param.name} - ${param.help}` : param.name}
      </label>
    );
  }
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {param.help ? `${param.name} - ${param.help}` : param.name}
      <input
        value={String(inputValue)}
        onChange={(e) => onChange(e.target.value)}
        className="eflux-input mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
      />
    </label>
  );
}

function isNumericParam(param: AlgorithmParam): boolean {
  return ["float", "number", "int", "integer"].includes(param.type);
}

function SliderField({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {label}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full accent-[var(--accent)]"
      />
    </label>
  );
}

function NumberField({
  label,
  value,
  step,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  step: string;
  min?: number;
  max?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs font-medium text-[var(--text-muted)]">
      {label}
      <input
        type="number"
        step={step}
        min={min}
        max={max}
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
            {r.ok && r.lesson && (
              <p className="mt-0.5 text-[11px] italic text-[var(--text-muted)]">💡 {r.lesson}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
