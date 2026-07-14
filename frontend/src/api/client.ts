import axios from "axios";

import type {
  AlgorithmInfo,
  BenchmarkComparison,
  BenchmarkDetail,
  BenchmarkSummary,
  ChatMessage,
  CompetitionDetail,
  CompetitionLeaderboard,
  CompetitionListItem,
  DeliveryProduct,
  ForecastHistoryRecord,
  ForecastTarget,
  LatestForecastResponse,
  LeaderboardHistory,
  LeaderboardOut,
  LeaderboardSession,
  ManagedVPPPerformance,
  ManagedVPP,
  MarketAgent,
  ArenaPayload,
  MarketEvent,
  MarketReflection,
  MarketSnapshot,
  OrderSubmitResponse,
  OrderPurpose,
  Participant,
  SessionInfo,
  SupplyCurve,
  TickEvent,
  TimeInForce,
  VPP,
} from "./types";

const TOKEN_KEY = "eflux.session_token";

type AuthExpiredHandler = () => void;

let authExpiredHandler: AuthExpiredHandler | null = null;

export function setAuthExpiredHandler(handler: AuthExpiredHandler | null): void {
  authExpiredHandler = handler;
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export const api = axios.create({
  baseURL: "/api",
  timeout: 10_000,
  withCredentials: true,
});

api.interceptors.request.use((config) => {
  const t = getToken();
  if (t) {
    config.headers = config.headers ?? {};
    (config.headers as Record<string, string>).Authorization = `Bearer ${t}`;
  }
  return config;
});

// Surface FastAPI error details ("detail": string | validation array) as the
// Error message, so the UI shows "qty: Input should be greater than 0" instead
// of axios' opaque "Request failed with status code 422".
api.interceptors.response.use(undefined, (error) => {
  const detail = error?.response?.data?.detail;
  let msg: string | undefined;
  if (typeof detail === "string") {
    msg = detail;
  } else if (Array.isArray(detail)) {
    msg = detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
        return loc ? `${loc}: ${d.msg}` : (d.msg ?? "");
      })
      .filter(Boolean)
      .join("; ");
  }
  if (!msg && error?.response?.status >= 500) {
    msg = import.meta.env.DEV
      ? `Server error (${error.response.status}). Check .run/backend.log for the failing request.`
      : `Server error (${error.response.status}). Please retry in a moment.`;
  }
  if (msg && error instanceof Error) error.message = msg;
  if (
    error?.response?.status === 401 &&
    error?.config?.url !== "/auth/logout" &&
    (msg === "invalid token" || msg === "missing bearer token")
  ) {
    setToken(null);
    authExpiredHandler?.();
  }
  return Promise.reject(error);
});

// --- Auth ---

export async function requestMagicLink(email: string): Promise<{ dev_token?: string }> {
  const { data } = await api.post("/auth/magic-link", { email });
  return data;
}

export async function consumeToken(token: string): Promise<SessionInfo> {
  const { data } = await api.post<SessionInfo>("/auth/consume", { token });
  return data;
}

export async function logout(): Promise<void> {
  await api.post("/auth/logout");
}

export interface CurrentUserInfo {
  id: number;
  email: string;
  role: string;
}

export async function getCurrentUser(): Promise<CurrentUserInfo> {
  const { data } = await api.get<CurrentUserInfo>("/auth/me");
  return data;
}

// --- Competitions ---

export async function listCompetitions(): Promise<CompetitionListItem[]> {
  const { data } = await api.get<CompetitionListItem[]>("/competitions");
  return data;
}

export async function fetchCompetition(slug: string): Promise<CompetitionDetail> {
  const { data } = await api.get<CompetitionDetail>(`/competitions/${slug}`);
  return data;
}

export async function fetchCompetitionLeaderboard(slug: string): Promise<CompetitionLeaderboard> {
  const { data } = await api.get<CompetitionLeaderboard>(`/competitions/${slug}/leaderboard`);
  return data;
}

// --- VPPs ---

export async function listVPPs(): Promise<VPP[]> {
  const { data } = await api.get<VPP[]>("/vpps");
  return data;
}

export async function listManagedVPPs(): Promise<ManagedVPP[]> {
  const { data } = await api.get<ManagedVPP[]>("/vpps/managed");
  return data;
}

export async function fetchManagedVPPPerformance(vppId: number): Promise<ManagedVPPPerformance> {
  const { data } = await api.get<ManagedVPPPerformance>(`/vpps/managed/${vppId}/performance`);
  return data;
}

export async function listAlgorithms(): Promise<AlgorithmInfo[]> {
  const { data } = await api.get<{ algorithms: AlgorithmInfo[] }>("/vpps/algorithms");
  return data.algorithms;
}

export async function createVPP(
  name: string,
  params: Record<string, number | string>,
): Promise<VPP> {
  const { data } = await api.post<VPP>("/vpps", { name, params });
  return data;
}

/** Deactivate a self-created (passive) VPP. Its resting orders are cancelled server-side. */
export async function deleteVPP(id: number): Promise<void> {
  await api.delete(`/vpps/${id}`);
}

// --- Managed agents (Tier 0: platform-hosted, LLM-steered) ---

export interface ManagedVPPCreatePayload {
  name: string;
  params: Record<string, number | string>;
  algorithm?: string;
  llm_enabled?: boolean;
  online_learning?: boolean;
  persona?: string | null;
  agent_params?: Record<string, number | string | boolean>;
  seed?: number | null;
  model?: string | null;
}

export async function createManagedVPP(payload: ManagedVPPCreatePayload): Promise<ManagedVPP> {
  const { data } = await api.post<ManagedVPP>("/vpps/managed", payload);
  return data;
}

export interface ManagedVPPUpdatePayload {
  params?: Record<string, number | string>;
  persona?: string | null;
  agent_params?: Record<string, number>;
  model?: string | null;
}

export async function updateManagedVPP(
  id: number,
  payload: ManagedVPPUpdatePayload,
): Promise<ManagedVPP> {
  const { data } = await api.patch<ManagedVPP>(`/vpps/managed/${id}`, payload);
  return data;
}

export async function deleteManagedVPP(id: number): Promise<void> {
  await api.delete(`/vpps/managed/${id}`);
}

export interface GuidancePayload {
  preferred_modes?: string[];
  avoid_modes?: string[];
  mode_pin?: string | null;
  risk_budget?: number;
  price_bias_bps?: number;
  soc_target?: number | null;
  execution_style?: string;
  lesson?: string;
  meta_control?: Record<string, number> | null;
}

/** Steer a managed agent with your own model (Tier A3). Values are clamped server-side. */
export async function putGuidance(managedId: number, payload: GuidancePayload): Promise<unknown> {
  const { data } = await api.put(`/vpps/managed/${managedId}/guidance`, payload);
  return data;
}

/** Hand steering back to the platform LLM strategist. */
export async function releaseGuidance(managedId: number): Promise<void> {
  await api.delete(`/vpps/managed/${managedId}/guidance`);
}

// --- Chatroom presence (managed agents) ---

export interface ChatPrefsPayload {
  style?: string | null;
  color?: string | null;
  avatar?: string | null;
}

/** Set the agent's chatroom voice/color/avatar (display only; no restart). */
export async function setChatPrefs(managedId: number, payload: ChatPrefsPayload): Promise<ManagedVPP> {
  const { data } = await api.put<ManagedVPP>(`/vpps/managed/${managedId}/chat`, payload);
  return data;
}

/** Post one line in the public chatroom as your managed agent. */
export async function sayInChatroom(managedId: number, text: string): Promise<ChatMessage> {
  const { data } = await api.post<ChatMessage>(`/vpps/managed/${managedId}/say`, { text });
  return data;
}

export interface ModelsInfo {
  models: string[];
  default: string;
}

export async function listModels(): Promise<ModelsInfo> {
  const { data } = await api.get<ModelsInfo>("/vpps/models");
  return data;
}

// --- API keys (Tier A1: drive your VPPs from an external app) ---

export interface ApiKeyInfo {
  name: string;
  prefix: string;
  created_at: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
}

/** A freshly minted key — `key` is the plaintext, shown to the user exactly once. */
export interface MintedApiKey extends ApiKeyInfo {
  key: string;
}

export async function listApiKeys(): Promise<ApiKeyInfo[]> {
  const { data } = await api.get<ApiKeyInfo[]>("/auth/api-keys");
  return data;
}

export async function mintApiKey(name: string): Promise<MintedApiKey> {
  const { data } = await api.post<MintedApiKey>("/auth/api-keys", { name });
  return data;
}

export async function revokeApiKey(prefix: string): Promise<void> {
  await api.delete(`/auth/api-keys/${prefix}`);
}

// --- Orders ---

export async function submitOrder(payload: {
  vpp_id: number;
  side: "buy" | "sell";
  price: number;
  qty_kwh: number;
  product_id: string;
  purpose: OrderPurpose;
  time_in_force: TimeInForce;
  ttl_sec?: number;
}): Promise<OrderSubmitResponse> {
  const { data } = await api.post<OrderSubmitResponse>("/orders", payload);
  return data;
}

// --- Market ---

export async function fetchSnapshot(depth = 10): Promise<MarketSnapshot> {
  const { data } = await api.get<MarketSnapshot>("/market/snapshot", { params: { depth } });
  return data;
}

export async function fetchProducts(): Promise<DeliveryProduct[]> {
  const { data } = await api.get<DeliveryProduct[]>("/market/products");
  return data;
}

export async function fetchRecentTrades(limit = 200): Promise<MarketEvent[]> {
  const { data } = await api.get<MarketEvent[]>("/market/trades", { params: { limit } });
  return data;
}

export async function fetchRecentTicks(limit = 100_000): Promise<TickEvent[]> {
  const { data } = await api.get<TickEvent[]>("/market/ticks", {
    params: { limit },
    timeout: 30_000,
  });
  return data;
}

export async function fetchParticipants(): Promise<Participant[]> {
  const { data } = await api.get<Participant[]>("/market/participants");
  return data;
}

export async function fetchSupplyCurve(): Promise<SupplyCurve> {
  const { data } = await api.get<SupplyCurve>("/market/supply_curve");
  return data;
}

export async function fetchMarketAgents(): Promise<MarketAgent[]> {
  const { data } = await api.get<MarketAgent[]>("/market/agents");
  return data;
}

export async function fetchArena(): Promise<ArenaPayload> {
  const { data } = await api.get<ArenaPayload>("/market/arena");
  return data;
}

export async function fetchMarketReflections(limit = 20): Promise<MarketReflection[]> {
  const { data } = await api.get<MarketReflection[]>("/market/reflections", { params: { limit } });
  return data;
}

export async function fetchChatter(limit = 40): Promise<ChatMessage[]> {
  const { data } = await api.get<ChatMessage[]>("/market/chatter", { params: { limit } });
  return data;
}

// --- Forecasts ---

export async function fetchLatestForecast(): Promise<LatestForecastResponse> {
  const { data } = await api.get<LatestForecastResponse>("/forecasts/latest");
  return data;
}

export async function fetchForecastHistory(
  target?: ForecastTarget,
  limit = 720,
): Promise<ForecastHistoryRecord[]> {
  const { data } = await api.get<ForecastHistoryRecord[]>("/forecasts/history", {
    params: { target, limit },
  });
  return data;
}

// --- Leaderboard (durable results across backend restarts) ---

export async function fetchLeaderboardSessions(): Promise<LeaderboardSession[]> {
  const { data } = await api.get<LeaderboardSession[]>("/leaderboard/sessions");
  return data;
}

export async function fetchLeaderboard(params: {
  scope: "session" | "alltime";
  session_id?: number;
  category?: string;
}): Promise<LeaderboardOut> {
  const { data } = await api.get<LeaderboardOut>("/leaderboard", { params });
  return data;
}

/** One identity's server-side equity curve. Pass exactly one of name / managed_def_id. */
export async function fetchLeaderboardHistory(params: {
  name?: string;
  managed_def_id?: number;
  session_id?: number;
  max_points?: number;
}): Promise<LeaderboardHistory> {
  const { data } = await api.get<LeaderboardHistory>("/leaderboard/history", { params });
  return data;
}

// --- Benchmarks (offline backtest artifacts) ---

export async function fetchBenchmarks(): Promise<BenchmarkSummary[]> {
  const { data } = await api.get<BenchmarkSummary[]>("/benchmarks");
  return data;
}

export async function fetchBenchmarkDetail(runId: string): Promise<BenchmarkDetail> {
  const { data } = await api.get<BenchmarkDetail>(`/benchmarks/${encodeURIComponent(runId)}`);
  return data;
}

export async function fetchBenchmarkComparison(
  left: string,
  right: string,
): Promise<BenchmarkComparison> {
  const { data } = await api.get<BenchmarkComparison>("/benchmarks/compare", {
    params: { left, right },
  });
  return data;
}

/** Chart URL for <img> tags (served through the same /api proxy). */
export function benchmarkChartUrl(runId: string, filename: string): string {
  return `/api/benchmarks/${encodeURIComponent(runId)}/charts/${encodeURIComponent(filename)}`;
}

// --- PPO renew (retrain on latest real data + hot-reload) ---

export type PpoRenewState = "idle" | "training" | "reloading" | "done" | "error";

export interface PpoStatus {
  state: PpoRenewState | string;
  started_at: string | null;
  finished_at: string | null;
  detail: string;
  reloaded: number;
  error: string | null;
  metrics: Record<string, unknown> | null;
}

export async function fetchPpoStatus(): Promise<PpoStatus> {
  const { data } = await api.get<PpoStatus>("/market/ppo/status");
  return data;
}

export async function renewPpos(days = 30): Promise<PpoStatus & { status: string }> {
  const { data } = await api.post("/market/ppo/renew", null, { params: { days } });
  return data;
}

// --- Health / meta ---

export type MarketMode = "p2p" | "realprice";

export interface MetaInfo {
  name: string;
  version: string;
  env: string;
  market_mode: MarketMode;
  market_speed: number;
  vpps_builtin: number;
}

export async function fetchMeta(): Promise<MetaInfo> {
  const { data } = await api.get<MetaInfo>("/");
  return data;
}
