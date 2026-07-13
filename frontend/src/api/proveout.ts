import { api } from "./client";

export interface ProveOutBattery {
  power_mw: number;
  energy_mwh: number;
  round_trip_efficiency: number;
  cycle_cost_per_mwh: number;
}

export interface ProveOutEndowment {
  battery?: ProveOutBattery;
  solar_mw: number;
  wind?: {
    power_mw: number;
    mean_speed_mps: number;
  };
  load?: {
    base_mw: number;
    profile: "residential" | "commercial" | "industrial" | "flat" | "ev";
    flexibility: number;
  };
  cash_usd: number;
}

export interface ProveOutWindow {
  start_date: string;
  end_date: string;
}

export interface ProveOutStrategy {
  algorithm: string;
  params?: Record<string, unknown>;
}

export interface CreateProveOutRun {
  label?: string;
  endowment: ProveOutEndowment;
  window: ProveOutWindow;
  strategy: ProveOutStrategy;
}

export type ProveOutStatus = "queued" | "running" | "done" | "failed";

export interface ProveOutRunCreated {
  run_id: string;
  status: ProveOutStatus;
}

export interface ProveOutRunSummary {
  run_id: string;
  label: string | null;
  status: ProveOutStatus;
  window_start: string;
  window_end: string;
  created_at: string;
  pnl_usd?: number;
  spread_capture_pct?: number;
}

export interface ProveOutDailyResult {
  date: string;
  pnl_usd: number;
  spread_capture_pct: number | null;
}

export interface ProveOutReport {
  pnl_usd: number;
  per_kw_month: number;
  spread_capture_pct: number | null;
  perfect_foresight_usd: number;
  baseline_hold_usd: number;
  max_drawdown_usd: number;
  trades: number;
  risk_rejections: number;
  imbalance_penalty_usd: number;
  degradation_cost_usd: number;
  ending_soc_kwh: number | null;
  energy_bought_kwh: number | null;
  energy_sold_kwh: number | null;
  solar_generation_kwh: number;
  wind_generation_kwh: number;
  load_consumption_kwh: number;
  ledger_breakdown: Record<string, number>;
  evidence_id: string | null;
  engine: string | null;
  price_resolution: string | null;
  audit_event_count?: number;
  replay_state_sha256?: string;
  replay_verified?: boolean;
  days: number;
  daily: ProveOutDailyResult[];
}

export interface ProveOutRunDetail extends ProveOutRunSummary {
  report?: ProveOutReport;
  error?: string;
  manifest?: Record<string, unknown>;
  evidence_sha256?: string;
}

export async function createProveOutRun(payload: CreateProveOutRun): Promise<ProveOutRunCreated> {
  const { data } = await api.post<ProveOutRunCreated>("/prove-out/runs", payload);
  return data;
}

export async function listProveOutRuns(limit = 20): Promise<ProveOutRunSummary[]> {
  const { data } = await api.get<ProveOutRunSummary[]>("/prove-out/runs", { params: { limit } });
  return data;
}

export async function fetchProveOutRun(id: string): Promise<ProveOutRunDetail> {
  const { data } = await api.get<ProveOutRunDetail>(`/prove-out/runs/${encodeURIComponent(id)}`);
  return data;
}

export async function downloadProveOutEvidence(id: string): Promise<void> {
  const { data } = await api.get<Blob>(`/prove-out/runs/${encodeURIComponent(id)}/evidence`, {
    responseType: "blob",
  });
  const url = URL.createObjectURL(data);
  const link = document.createElement("a");
  link.href = url;
  link.download = `prove-out-${id}-evidence.json`;
  link.click();
  URL.revokeObjectURL(url);
}
