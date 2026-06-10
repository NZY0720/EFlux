export interface MarketSnapshot {
  sim_ts: string;
  speed: number;
  best_bid: string | null;
  best_ask: string | null;
  last_price: string | null;
  bids: [string, string][];
  asks: [string, string][];
  num_builtin_vpps: number;
  data_source: DataSourceStatus;
}

export interface DataSourceEntry {
  component: string;
  status: "real" | "fallback" | "synthetic" | string;
  source: string;
  detail: string;
}

export interface DataSourceStatus {
  checked_at: string;
  sim_ts: string;
  summary: string;
  sources: DataSourceEntry[];
}

export interface VPP {
  id: number;
  name: string;
  params: Record<string, number>;
  is_active: boolean;
  is_external: boolean;
  created_at: string;
}

export interface ManagedVPP {
  id: number;
  name: string;
  params: Record<string, number>;
  is_active: boolean;
  is_external: boolean;
  agent_kind: string;
  strategy: string;
  llm_live: boolean;
  llm_status: string;
  llm_health_state: "live" | "degraded" | "offline" | string;
}

export interface ReflectionEntry {
  ts: string;
  ok: boolean;
  price_adjust: number;
  qty_scale: number;
  rationale: string;
  error: string | null;
}

export interface LLMHealth {
  ok_count: number;
  fail_count: number;
  last_ok_ts: string | null;
  state: "live" | "degraded" | "offline" | string;
}

export interface ManagedTrade {
  trade_id: number;
  side: "buy" | "sell" | string;
  price: string;
  qty: string;
  cash: string;
  counterparty_vpp_id: number;
  buy_vpp_id: number;
  sell_vpp_id: number;
  sim_ts: string;
  wall_ts: string;
}

export interface ManagedVPPPerformance {
  id: number;
  name: string;
  pnl: string;
  cumulative_energy_bought_kwh: number;
  cumulative_energy_sold_kwh: number;
  soc_kwh: number;
  soc_frac: number;
  recent_trades: ManagedTrade[];
  reflections: ReflectionEntry[];
  llm_health: LLMHealth | null;
}

export interface SessionInfo {
  session_token: string;
  user_id: number;
  email: string;
}

export interface OrderSubmitResponse {
  order_id: number;
  remaining_qty: string;
  trades: TradeEvent[];
}

export type EventKind = "order.submitted" | "order.cancelled" | "trade" | "tick";

export interface BaseEvent {
  kind: EventKind;
  sim_ts: string;
  wall_ts: string;
}

export interface OrderEvent extends BaseEvent {
  kind: "order.submitted" | "order.cancelled";
  order_id: number;
  vpp_id: number;
  side: "buy" | "sell";
  price: string;
  qty: string;
  remaining_qty: string;
}

export interface TradeEvent extends BaseEvent {
  kind: "trade";
  trade_id: number;
  buy_order_id: number;
  sell_order_id: number;
  buy_vpp_id: number;
  sell_vpp_id: number;
  price: string;
  qty: string;
}

export interface TickEvent extends BaseEvent {
  kind: "tick";
  tick_no: number;
  best_bid: string | null;
  best_ask: string | null;
  last_price: string | null;
  bid_depth: string;
  ask_depth: string;
}

export type MarketEvent = OrderEvent | TradeEvent | TickEvent;
