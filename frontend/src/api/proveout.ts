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
  spread_capture_pct: number;
}

export interface ProveOutReport {
  pnl_usd: number;
  per_kw_month: number;
  spread_capture_pct: number;
  perfect_foresight_usd: number;
  baseline_hold_usd: number;
  max_drawdown_usd: number;
  trades: number;
  risk_rejections: number;
  imbalance_penalty_usd: number;
  days: number;
  daily: ProveOutDailyResult[];
}

export interface ProveOutRunDetail extends ProveOutRunSummary {
  report?: ProveOutReport;
  error?: string;
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
