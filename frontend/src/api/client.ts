import axios from "axios";

import type {
  ManagedVPPPerformance,
  ManagedVPP,
  MarketAgent,
  MarketEvent,
  MarketReflection,
  MarketSnapshot,
  OrderSubmitResponse,
  Participant,
  SessionInfo,
  SupplyCurve,
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
  if (msg && error instanceof Error) error.message = msg;
  if (
    error?.response?.status === 401 &&
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
  setToken(data.session_token);
  return data;
}

export function logout(): void {
  setToken(null);
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

export async function createVPP(name: string, params: Record<string, number>): Promise<VPP> {
  const { data } = await api.post<VPP>("/vpps", { name, params });
  return data;
}

// --- Managed agents (Tier 0: platform-hosted, LLM-steered) ---

export interface ManagedVPPCreatePayload {
  name: string;
  params: Record<string, number>;
  persona?: string | null;
  agent_params?: Record<string, number>;
  seed?: number | null;
}

export async function createManagedVPP(payload: ManagedVPPCreatePayload): Promise<ManagedVPP> {
  const { data } = await api.post<ManagedVPP>("/vpps/managed", payload);
  return data;
}

export interface ManagedVPPUpdatePayload {
  params?: Record<string, number>;
  persona?: string | null;
  agent_params?: Record<string, number>;
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

// --- Orders ---

export async function submitOrder(payload: {
  vpp_id: number;
  side: "buy" | "sell";
  price: number;
  qty: number;
}): Promise<OrderSubmitResponse> {
  const { data } = await api.post<OrderSubmitResponse>("/orders", payload);
  return data;
}

// --- Market ---

export async function fetchSnapshot(depth = 10): Promise<MarketSnapshot> {
  const { data } = await api.get<MarketSnapshot>("/market/snapshot", { params: { depth } });
  return data;
}

export async function fetchRecentTrades(limit = 200): Promise<MarketEvent[]> {
  const { data } = await api.get<MarketEvent[]>("/market/trades", { params: { limit } });
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

export async function fetchMarketReflections(limit = 20): Promise<MarketReflection[]> {
  const { data } = await api.get<MarketReflection[]>("/market/reflections", { params: { limit } });
  return data;
}

export async function setMarketSpeed(speed: number): Promise<{ speed: number; is_realtime: boolean }> {
  const { data } = await api.post("/market/speed", { speed });
  return data;
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
