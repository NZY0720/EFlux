import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Bot, ChevronDown, ChevronRight, MapPinned, PlusCircle } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { api, createManagedVPP, createVPP, listAlgorithms, listModels } from "../api/client";
import type { AlgorithmInfo } from "../api/types";
import { CardTitle, DashboardCard, StatusPill } from "../components/DashboardCard";
import { AlgorithmParamField, NumberField, SelectField, SliderField, TextNumberField } from "./vpps/LegacyVppParts";

type AlgorithmParamValue = number | string | boolean;
const LOAD_PROFILES = ["residential", "industrial", "commercial", "flat"];
const OFFLINE_PRESETS = {
  "Solar Trader": { pv: 8, batt: 8, load: 2, wind: 0, loadProfile: "residential", algorithm: "ppo", llm: true, online: true, beta: 0.45 },
  "Battery Arbitrageur": { pv: 2, batt: 20, load: 1.5, wind: 0, loadProfile: "flat", algorithm: "ppo", llm: true, online: true, beta: 0.25 },
  "Demand Optimizer": { pv: 4, batt: 10, load: 5, wind: 0, loadProfile: "commercial", algorithm: "ppo", llm: true, online: true, beta: 0.8 },
  Custom: null,
} as const;
type PresetValues = Exclude<(typeof OFFLINE_PRESETS)[keyof typeof OFFLINE_PRESETS], null>;
type Presets = Record<string, PresetValues | null>;
type PresetName = keyof typeof OFFLINE_PRESETS;

export default function VppDeploy() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [preset, setPreset] = useState<PresetName>("Solar Trader");
  const [presets, setPresets] = useState<Presets>(OFFLINE_PRESETS);
  const [name, setName] = useState("");
  const [pv, setPv] = useState(6);
  const [batt, setBatt] = useState(12);
  const [load, setLoad] = useState(2);
  const [wind, setWind] = useState(0);
  const [loadProfile, setLoadProfile] = useState("residential");
  const [algorithm, setAlgorithm] = useState("ppo");
  const [llm, setLlm] = useState(true);
  const [online, setOnline] = useState(true);
  const [beta, setBeta] = useState(0.5);
  const [model, setModel] = useState("");
  const [persona, setPersona] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [advancedParams, setAdvancedParams] = useState<Record<string, AlgorithmParamValue>>({});
  const [models, setModels] = useState<string[]>([]);
  const [algorithms, setAlgorithms] = useState<AlgorithmInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualName, setManualName] = useState("");
  const [manualPv, setManualPv] = useState(6);
  const [manualBatt, setManualBatt] = useState(12);
  const [manualLoad, setManualLoad] = useState(2);
  const [manualWind, setManualWind] = useState(0);
  const [manualProfile, setManualProfile] = useState("residential");
  const [manualAdvanced, setManualAdvanced] = useState(false);
  const [pvLat, setPvLat] = useState("");
  const [pvLon, setPvLon] = useState("");
  const [pvTilt, setPvTilt] = useState(30);
  const [pvAzimuth, setPvAzimuth] = useState(180);

  useEffect(() => {
    listModels().then((result) => { setModels(result.models); setModel((current) => current || result.default); }).catch(() => {});
    listAlgorithms().then((result) => { setAlgorithms(result); setAlgorithm((current) => result.some((item) => item.id === current) ? current : (result[0]?.id ?? "ppo")); }).catch(() => {});
    api.get<Record<string, PresetValues>>("/vpps/presets").then(({ data }) => setPresets({ ...data, Custom: null })).catch(() => {});
  }, []);

  const selectedAlgorithm = useMemo(() => algorithms.find((item) => item.id === algorithm) ?? {
    id: "ppo", label: "PPO", description: "Structured-policy tactical executor over the shared action space.", llm_capable: true, supports_online_learning: true,
    params: [{ name: "demand_beta", type: "float", default: 0.5, min: 0, max: 1, help: "Demand response sensitivity." }],
  }, [algorithm, algorithms]);
  const supportsDemandBeta = selectedAlgorithm.params.some((param) => param.name === "demand_beta");
  const isPpo = selectedAlgorithm.id === "ppo";
  const extraParams = selectedAlgorithm.params.filter((param) => param.name !== "demand_beta");

  const choosePreset = (choice: PresetName) => {
    setPreset(choice);
    const values = presets[choice];
    if (!values) return;
    setPv(values.pv); setBatt(values.batt); setLoad(values.load); setWind(values.wind); setLoadProfile(values.loadProfile);
    setAlgorithm(values.algorithm); setLlm(values.llm); setOnline(values.online); setBeta(values.beta);
  };
  const selectAlgorithm = (value: string) => { setAlgorithm(value); setAdvancedParams({}); setAdvancedOpen(false); };
  const deploy = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(null);
    try {
      const agentParams: Record<string, AlgorithmParamValue> = { ...advancedParams, ...(supportsDemandBeta ? { demand_beta: beta } : {}) };
      const created = await createManagedVPP({
        name: name.trim(),
        params: { pv_kw_peak: pv, battery_kwh: batt, load_kw_base: load, load_profile: loadProfile, ...(wind > 0 ? { wind_kw_rated: wind } : {}) },
        algorithm: selectedAlgorithm.id, llm_enabled: llm,
        ...(isPpo ? { online_learning: online } : {}),
        ...(Object.keys(agentParams).length > 0 ? { agent_params: agentParams } : {}),
        ...(llm ? { persona: persona.trim() || null, model: model || null } : {}),
      });
      navigate(`/vpps/${created.id}`);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };
  const createManual = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      const params: Record<string, number | string> = { pv_kw_peak: manualPv, battery_kwh: manualBatt, load_kw_base: manualLoad, load_profile: manualProfile };
      if (manualWind > 0) params.wind_kw_rated = manualWind;
      if (pvLat !== "" && pvLon !== "") { params.pv_lat = Number(pvLat); params.pv_lon = Number(pvLon); params.pv_tilt = pvTilt; params.pv_azimuth = pvAzimuth; }
      const created = await createVPP(manualName.trim(), params);
      navigate(`/vpps/${created.id}`);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };

  const steps = ["Preset", "Assets", "Algorithm & LLM", "Budget & risk", "Confirm & deploy"];
  return <div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-5 md:p-6">
    <DashboardCard>
      <CardTitle icon={Bot}>Deploy a cloud-hosted agent</CardTitle>
      <p className="mb-4 text-sm text-[var(--text-muted)]">We run a trading agent for you — no code or infrastructure. Every order passes the platform risk gate.</p>
      <ol className="mb-5 grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">{steps.map((label, index) => <li key={label} className={`rounded-md border px-2 py-2 ${step === index + 1 ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]" : "border-[var(--border)] text-[var(--text-muted)]"}`}>{index + 1}. {label}</li>)}</ol>
      <form onSubmit={deploy} className="space-y-4">
        {step === 1 && <div className="grid gap-2 sm:grid-cols-2"><input required value={name} onChange={(e) => setName(e.target.value)} placeholder="agent name" className="eflux-input rounded-md px-3 py-2 text-sm outline-none sm:col-span-2" />{Object.keys(presets).map((choice) => <button key={choice} type="button" onClick={() => choosePreset(choice as PresetName)} className={`rounded-lg border p-3 text-left text-sm ${preset === choice ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)] hover:bg-[var(--surface-hover)]"}`}><span className="font-semibold">{choice}</span><span className="mt-1 block text-xs text-[var(--text-muted)]">{choice === "Custom" ? "Keep every setting editable." : `PV ${presets[choice]!.pv}kW · Battery ${presets[choice]!.batt}kWh`}</span></button>)}</div>}
        {step === 2 && <div className="space-y-3"><div className="grid grid-cols-2 gap-2"><NumberField label="PV peak (kW)" value={pv} step="0.5" onChange={setPv} /><NumberField label="Battery (kWh)" value={batt} step="1" onChange={setBatt} /><NumberField label="Load (kW)" value={load} step="0.5" onChange={setLoad} /><NumberField label="Wind (kW)" value={wind} step="0.5" onChange={setWind} /></div><SelectField label="Load profile" value={loadProfile} options={LOAD_PROFILES} onChange={setLoadProfile} /></div>}
        {step === 3 && <div className="space-y-3"><SelectField label="Algorithm" value={algorithm} options={algorithms.map((item) => ({ value: item.id, label: item.label }))} onChange={selectAlgorithm} /><p className="text-xs text-[var(--text-muted)]">{selectedAlgorithm.description}</p><label className="flex items-center gap-2 text-xs font-medium text-[var(--text)]"><input type="checkbox" checked={llm} onChange={(e) => setLlm(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" /> Enable LLM strategist <StatusPill tone="accent" className="py-0 text-[10px]">{llm ? `LLM + ${selectedAlgorithm.label}` : selectedAlgorithm.label}</StatusPill></label>{llm && <><SelectField label="LLM model" value={model} options={models} onChange={setModel} /><label className="block text-xs font-medium text-[var(--text-muted)]">Strategy brief (persona) — optional<textarea value={persona} onChange={(e) => setPersona(e.target.value)} rows={4} maxLength={600} className="eflux-input mt-1 w-full resize-none rounded-md px-3 py-2 text-sm outline-none" /></label></>}</div>}
        {step === 4 && <div className="space-y-3">{isPpo && <label className="flex items-center gap-2 text-xs font-medium text-[var(--text-muted)]"><input type="checkbox" checked={online} onChange={(e) => setOnline(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" /> Online learning</label>}{supportsDemandBeta && <SliderField label={`Demand response (demand_beta ${beta.toFixed(2)})`} value={beta} min={0} max={1} step={0.05} onChange={setBeta} />}{extraParams.length > 0 && <><button type="button" onClick={() => setAdvancedOpen((open) => !open)} className="eflux-btn h-8 px-3 text-xs">{advancedOpen ? "Hide algorithm parameters" : "Algorithm parameters (advanced)"}</button>{advancedOpen && <div className="eflux-inset grid gap-2 rounded-lg p-3 sm:grid-cols-2">{extraParams.map((param) => <AlgorithmParamField key={param.name} param={param} value={advancedParams[param.name]} onChange={(value) => setAdvancedParams((current) => ({ ...current, [param.name]: value }))} />)}</div>}</>}</div>}
        {step === 5 && <div className="eflux-inset space-y-2 rounded-lg p-3 text-sm"><p><span className="font-semibold">{name || "Unnamed agent"}</span> · {preset}</p><p className="text-[var(--text-muted)]">PV {pv}kW / Battery {batt}kWh / Load {load}kW · {llm ? `LLM + ${selectedAlgorithm.label}` : selectedAlgorithm.label}</p></div>}
        <div className="flex items-center justify-between gap-2 border-t border-[var(--border)] pt-4"><button type="button" disabled={step === 1} onClick={() => setStep((current) => current - 1)} className="eflux-btn h-9 px-4 text-sm disabled:opacity-50">Back</button>{step < 5 ? <button type="button" onClick={() => setStep((current) => current + 1)} className="eflux-btn eflux-btn-primary h-9 px-4 text-sm">Continue</button> : <button disabled={busy} className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50"><Bot size={15} />{busy ? "Deploying…" : "Deploy agent"}</button>}</div>
      </form>
    </DashboardCard>
    <DashboardCard><button type="button" onClick={() => setManualOpen((open) => !open)} className="flex w-full items-center justify-between text-left"><span className="flex items-center gap-2 text-sm font-semibold text-[var(--text)]"><PlusCircle size={16} className="text-[var(--accent)]" /> Create a manually controlled VPP</span>{manualOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>{manualOpen && <form onSubmit={createManual} className="mt-4 space-y-3 border-t border-[var(--border)] pt-4"><input required value={manualName} onChange={(e) => setManualName(e.target.value)} placeholder="name" className="eflux-input w-full rounded-md px-3 py-2 text-sm outline-none" /><div className="grid grid-cols-2 gap-2"><NumberField label="PV peak (kW)" value={manualPv} step="0.5" onChange={setManualPv} /><NumberField label="Battery (kWh)" value={manualBatt} step="1" onChange={setManualBatt} /><NumberField label="Load (kW)" value={manualLoad} step="0.5" onChange={setManualLoad} /><NumberField label="Wind (kW)" value={manualWind} step="0.5" onChange={setManualWind} /></div><SelectField label="Load profile" value={manualProfile} options={LOAD_PROFILES} onChange={setManualProfile} /><button type="button" onClick={() => setManualAdvanced((open) => !open)} className="eflux-btn h-8 px-3 text-xs"><MapPinned size={14} />{manualAdvanced ? "Hide advanced" : "Show advanced"}</button>{manualAdvanced && <div className="eflux-inset grid grid-cols-2 gap-2 rounded-lg p-3"><TextNumberField label="Latitude" value={pvLat} step="0.01" placeholder="22.28" onChange={setPvLat} /><TextNumberField label="Longitude" value={pvLon} step="0.01" placeholder="114.13" onChange={setPvLon} /><NumberField label="Tilt (deg)" value={pvTilt} step="1" onChange={setPvTilt} /><NumberField label="Azimuth (deg from N)" value={pvAzimuth} step="5" onChange={setPvAzimuth} /></div>}<button disabled={busy} className="eflux-btn eflux-btn-primary h-9 px-4 text-sm disabled:opacity-50">{busy ? "Creating…" : "Create VPP"}</button></form>}</DashboardCard>
    {error && <p className="rounded-lg bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]">{error}</p>}
  </div>;
}
