import axios from "axios";

import type {
  ManagedVPPPerformance,
  ManagedVPP,
  MarketSnapshot,
  OrderSubmitResponse,
  Participant,
  SessionInfo,
  TradeEvent,
  VPP,
} from "./types";

const TOKEN_KEY = "eflux.session_token";

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

export async function fetchRecentTrades(limit = 200): Promise<TradeEvent[]> {
  const { data } = await api.get<TradeEvent[]>("/market/trades", { params: { limit } });
  return data;
}

export async function fetchParticipants(): Promise<Participant[]> {
  const { data } = await api.get<Participant[]>("/market/participants");
  return data;
}

// --- Health / meta ---

export async function fetchMeta(): Promise<{
  name: string;
  version: string;
  env: string;
  market_speed: number;
  vpps_builtin: number;
}> {
  const { data } = await api.get("/");
  return data;
}
