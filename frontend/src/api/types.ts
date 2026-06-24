export interface MarketBalance {
  renewable_kw: number;
  load_kw: number;
  gas_capacity_kw: number;
  net_kw: number;
  supply_demand_ratio: number | null;
  bid_depth_kwh: number;
  ask_depth_kwh: number;
}

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
  balance: MarketBalance;
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
  price_adjust?: number | null;
  qty_scale?: number | null;
  preferred_modes?: string[] | null;
  avoid_modes?: string[] | null;
  risk_budget?: number | null;
  soc_target?: number | null;
  execution_style?: string | null;
  rationale: string;
  lesson?: string | null;
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

export interface Participant {
  id: number;
  name: string;
  kind: "builtin" | "external" | string;
  strategy: string | null;
}

export type AgentCategory = "solar" | "wind" | "gas" | "battery_load" | "llm" | "external";

export interface SupplyCurveOrder {
  price: string;
  qty: string;
  category: AgentCategory | string;
  vpp_name: string | null;
}

export interface SupplyCurve {
  sim_ts: string;
  asks: SupplyCurveOrder[]; // cheapest first — the merit order
  bids: SupplyCurveOrder[]; // highest first — the demand curve
}

export interface MarketAgent {
  id: number;
  name: string;
  strategy: string;
  category: AgentCategory | string;
  is_llm: boolean;
  llm_health_state: "live" | "degraded" | "offline" | null;
  pv_kw_peak: number;
  wind_kw_rated: number;
  battery_kwh: number;
  battery_kw_max: number;
  load_kw_base: number;
  gas_kw_max: number;
  gas_cost_per_kwh: number;
  pnl: string;
  soc_kwh: number;
  soc_frac: number;
  pv_kw: number;
  wind_kw: number;
  load_kw: number;
  net_kw: number;
  energy_bought_kwh: number;
  energy_sold_kwh: number;
  recent_trade_count: number;
}

export interface MarketReflection {
  vpp_id: number;
  vpp_name: string;
  health_state: "live" | "degraded" | "offline" | string;
  ts: string;
  ok: boolean;
  price_adjust?: number | null;
  qty_scale?: number | null;
  preferred_modes?: string[] | null;
  avoid_modes?: string[] | null;
  risk_budget?: number | null;
  soc_target?: number | null;
  execution_style?: string | null;
  rationale: string;
  lesson?: string | null;
  error: string | null;
}

export interface SessionInfo {
  session_token: string;
  user_id: number;
  email: string;
}

export interface OrderSubmitResponse {
  order_id: number;
  remaining_qty: string;
  /** Sim time the unfilled remainder is swept by the order TTL; null = rests. */
  expires_at_sim?: string | null;
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
