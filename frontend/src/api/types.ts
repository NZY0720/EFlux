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
  external_market: ExternalMarket;
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

export interface ExternalMarket {
  region: string;
  node: string;
  raw_lmp: string;
  p2p_anchor_price: string;
  import_price: string;
  export_price: string;
  interval_start: string | null;
  interval_end: string | null;
  currency: string;
  unit: string;
  status: string;
  source: string;
  detail: string;
  fetched_at: string;
}

export interface VPP {
  id: number;
  name: string;
  params: Record<string, number>;
  is_active: boolean;
  is_external: boolean;
  created_at: string;
}

export interface AlgorithmParam {
  name: string;
  type: string;
  default: number | string | boolean | null;
  min?: number | null;
  max?: number | null;
  help: string;
}

export interface AlgorithmInfo {
  id: string;
  label: string;
  description: string;
  /** Every base algorithm can be paired with the LLM strategist via the llm_enabled toggle. */
  llm_capable: boolean;
  supports_online_learning: boolean;
  params: AlgorithmParam[];
}

export interface ManagedVPP {
  id: number;
  vpp_id: number;
  name: string;
  params: Record<string, number>;
  is_active: boolean;
  is_external: boolean;
  algorithm: string;
  /** Whether the LLM strategist is layered on the base algorithm (drives the "LLM + <ALGO>" label). */
  llm_enabled: boolean;
  agent_kind: string;
  strategy: string;
  llm_live: boolean;
  llm_status: string;
  llm_health_state: "live" | "degraded" | "offline" | string;
  persona?: string | null;
  model?: string | null;
  /** Who steers the agent: platform LLM, the owner's own model (Tier A3), or nobody. */
  guidance_source?: "platform" | "external" | "none" | string;
  /** Chatroom presence preferences. */
  chat_style?: string | null;
  chat_color?: string | null;
  chat_avatar?: string | null;
}

export interface ChatMessage {
  name: string;
  wall_ts: string;
  text: string;
  /** Owner-picked display color/emoji, and whether the LLM or the owner spoke. */
  color?: string | null;
  avatar?: string | null;
  source?: "agent" | "owner" | string;
}

export interface ReflectionEntry {
  ts: string;
  ok: boolean;
  price_adjust?: number | null;
  qty_scale?: number | null;
  preferred_modes?: string[] | null;
  avoid_modes?: string[] | null;
  mode_pin?: string | null;
  risk_budget?: number | null;
  price_bias_bps?: number | null;
  soc_target?: number | null;
  execution_style?: string | null;
  rationale: string;
  lesson?: string | null;
  meta_control?: PpoMetaControl | null;
  error: string | null;
}

export interface LLMHealth {
  ok_count: number;
  fail_count: number;
  last_ok_ts: string | null;
  state: "live" | "degraded" | "offline" | string;
}

export interface ManagedTrade {
  trade_id: number | string;
  kind?: string | null;
  side: "buy" | "sell" | string;
  price: string;
  raw_lmp?: string | null;
  qty: string;
  cash: string;
  counterparty?: string | null;
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
  mirror_of: string | null;
  llm_health_state: "live" | "degraded" | "offline" | null;
  /** The strategist's LLM model (null for non-LLM agents or unconfigured LLM). */
  llm_model?: string | null;
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
  trade_count: number;
  recent_trade_count: number;
  fallback_count?: number;
  veto_hold_count?: number;
  risk_rejections?: number;
  decide_ticks?: number;
  guidance_change_rate?: number | null;
  mode_override_rate?: number | null;
  avg_price_dev_bps?: number | null;
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
  mode_pin?: string | null;
  risk_budget?: number | null;
  price_bias_bps?: number | null;
  soc_target?: number | null;
  execution_style?: string | null;
  rationale: string;
  lesson?: string | null;
  meta_control?: PpoMetaControl | null;
  error: string | null;
}

export interface PpoMetaControl {
  w_imbalance_mult?: number;
  w_soc_mult?: number;
  w_degrade_mult?: number;
  lr?: number;
  entropy_coef?: number;
  kl_target?: number;
  mode_reg_coef?: number;
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
  trades: Array<TradeEvent | ExternalTradeEvent>;
}

export type EventKind = "order.submitted" | "order.cancelled" | "trade" | "external.trade" | "tick";

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

export interface ExternalTradeEvent extends BaseEvent {
  kind: "external.trade";
  external_trade_id: number;
  vpp_id: number;
  side: "buy" | "sell" | string;
  price: string;
  raw_lmp: string;
  qty: string;
  region: string;
  node: string;
  counterparty: string;
  interval_start: string | null;
  interval_end: string | null;
}

export interface TickEvent extends BaseEvent {
  kind: "tick";
  tick_no: number;
  best_bid: string | null;
  best_ask: string | null;
  last_price: string | null;
  external_price: string | null;
  bid_depth: string;
  ask_depth: string;
}

export type MarketEvent = OrderEvent | TradeEvent | ExternalTradeEvent | TickEvent;

// --- Forecasts ---

export type ForecastTarget = "price_real" | "price_p2p" | "ghi" | "temp_air" | "wind_speed";
export type ForecastHorizon = "5m" | "1h" | "12h";

export interface ForecastEstimate {
  value: number | null;
  stderr: number | null;
}

export type LatestForecastTarget = Record<ForecastHorizon, ForecastEstimate>;

export interface LatestForecastResponse extends Record<ForecastTarget, LatestForecastTarget> {
  as_of: string;
  model_version: string;
  /** False while the price models have no observations — values are placeholders. */
  warm?: boolean;
}

export type ForecastHistoryBundle = Partial<Record<ForecastHorizon, number | null>>;

export interface ForecastHistoryRecord {
  as_of: string;
  forecasts: Partial<Record<ForecastTarget, ForecastHistoryBundle>>;
  realized: Partial<Record<ForecastTarget, number | null>>;
}

// --- Leaderboard (durable results across backend restarts) ---

export interface LeaderboardSession {
  id: number;
  market_mode: "p2p" | "realprice" | string;
  started_at: string;
  ended_at: string | null;
  price_ref: string;
  is_current: boolean;
}

export interface LeaderboardRow {
  /** Stable identity across restarts: "name:<name>" | "managed:<def_id>". */
  identity: string;
  name: string;
  managed_def_id: number | null;
  category: AgentCategory | string;
  strategy: string;
  is_llm: boolean;
  llm_model: string | null;
  pnl_usd: string;
  /** Score v1 — endowment- and duration-normalized PnL (see backend stats/score.py). */
  score: number;
  trade_count: number;
  energy_bought_kwh: number;
  energy_sold_kwh: number;
  soc_frac: number;
  sessions_count: number;
  hours: number;
  last_seen_at: string;
}

export interface LeaderboardOut {
  scope: "session" | "alltime";
  session_id: number | null;
  market_mode: "p2p" | "realprice" | string;
  rows: LeaderboardRow[];
}

export interface EquityPoint {
  tick_no: number;
  sim_ts: string;
  wall_ts: string;
  pnl_usd: string;
  soc_frac: number;
}

export interface LeaderboardHistory {
  identity: string;
  session_id: number;
  points: EquityPoint[];
}

// --- Benchmarks (offline backtest artifacts) ---

export interface BenchmarkSummary {
  run_id: string;
  market_mode: "p2p" | "realprice" | string;
  status: "ok" | "failed" | "incomplete" | string;
  start: string | null;
  end: string | null;
  months: number | null;
  tick_seconds: number | null;
  llm_mode: string | null;
  llm_calls: number | null;
  expected_llm_calls: number | null;
  ticks_run: number | null;
  live_participants: number | null;
  finished_at: string | null;
  charts: string[];
}

export interface BenchmarkParticipant {
  vpp_id: number;
  name: string;
  strategy: string;
  is_llm: boolean;
  mirror_of: string;
  group_id: string;
  realized_pnl: number;
  mark_to_market: number;
  energy_bought_kwh: number;
  energy_sold_kwh: number;
  trade_count: number;
  risk_rejections: number;
  unresolved_imbalance_kwh: number;
  final_soc_frac: number;
}

export interface BenchmarkDetail {
  run_id: string;
  manifest: Record<string, unknown>;
  participants: BenchmarkParticipant[];
  groups: Record<string, unknown>[];
  charts: string[];
}
