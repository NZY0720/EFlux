/* eslint-disable */
/**
 * Generated from docs/openapi.json by scripts/generate_openapi_ts.py.
 * Do not edit by hand; run `pnpm -C frontend generate:api`.
 */

export interface components {
  schemas: {
    AgentOut: { id : number; name : string; strategy : string; category : string; is_llm : boolean; mirror_of?: string | null; llm_health_state : string | null; llm_model?: string | null; archetype : string; resources : Array<string>; pv_kw_peak : number; wind_kw_rated : number; battery_kwh : number; battery_kw_max : number; load_kw_base : number; gas_kw_max : number; gas_cost_per_mwh : number; pnl : string; soc_kwh : number; soc_frac : number; pv_kw : number; wind_kw : number; load_kw : number; net_kw : number; energy_bought_kwh : number; energy_sold_kwh : number; trade_count : number; recent_trade_count : number; observation_min : number; fallback_count?: number; veto_hold_count?: number; risk_rejections?: number; decide_ticks?: number; guidance_change_rate?: number | null; mode_override_rate?: number | null; avg_price_dev_bps?: number | null; };
    AlgorithmOut: { id : string; label : string; description : string; llm_capable : boolean; supports_online_learning : boolean; params : Array<components["schemas"]["AlgorithmParamOut"]>; };
    AlgorithmParamOut: { name : string; type : string; default?: unknown; min?: number | null; max?: number | null; help : string; };
    AlgorithmsOut: { algorithms : Array<components["schemas"]["AlgorithmOut"]>; default : string; default_llm_enabled?: boolean; };
    ApiKeyMintRequest: { name : string; };
    ApiKeyMintResponse: { name : string; key : string; prefix : string; created_at : string; };
    ApiKeyOut: { name : string; prefix : string; created_at : string; last_used_at?: string | null; revoked_at?: string | null; };
    ArenaOut: { min_trades : number; min_observation_min : number; agents : Array<components["schemas"]["AgentOut"]>; };
    BatchCancelResult: { order_id : number; ok : boolean; };
    BatchOrderItem: { vpp_id : number; side : "buy" | "sell"; price : number | string; qty : number | string; client_ref?: string | null; };
    BatchOrderResult: { index : number; client_ref?: string | null; status : string; order_id?: number | null; remaining_qty?: string | null; expires_at_sim?: string | null; reason?: string | null; trades?: Array<{ [key: string]: unknown; }>; };
    BatchResult: { protocol_version : number; tick_id : number; results : Array<components["schemas"]["BatchOrderResult"]>; cancelled : Array<components["schemas"]["BatchCancelResult"]>; rate_limit_remaining : number; };
    BatteryIn: { power_mw : number; energy_mwh : number; round_trip_efficiency : number; cycle_cost_per_mwh?: number; };
    BenchmarkDetail: { run_id : string; manifest : { [key: string]: unknown; }; participants : Array<{ [key: string]: unknown; }>; groups : Array<{ [key: string]: unknown; }>; charts : Array<string>; };
    BenchmarkSummary: { run_id : string; market_mode : string; status : string; start?: string | null; end?: string | null; months?: number | null; tick_seconds?: number | null; llm_mode?: string | null; llm_calls?: number | null; expected_llm_calls?: number | null; ticks_run?: number | null; live_participants?: number | null; finished_at?: string | null; charts : Array<string>; };
    ChatMessageOut: { name : string; wall_ts : string; text : string; color?: string | null; avatar?: string | null; source?: string; };
    ChatPostOut: { name : string; wall_ts : string; text : string; color : string | null; avatar : string | null; source : string; };
    ChatPrefsIn: { style?: string | null; color?: string | null; avatar?: string | null; };
    CompetitionDetailOut: { id : number; slug : string; title : string; status : string; tracks : Array<string>; submission_counts : { [key: string]: number; }; description : string; rulesets : Array<components["schemas"]["CompetitionRuleSetOut"]>; practice_seed_values : Array<number>; hidden_seed_count : number; holdout_seed_count : number; };
    CompetitionListOut: { id : number; slug : string; title : string; status : string; tracks : Array<string>; submission_counts : { [key: string]: number; }; };
    CompetitionRuleSetOut: { id : number; version : string; track : string; config : { [key: string]: unknown; }; created_at : string; };
    ConsumeRequest: { token : string; };
    CurrentUserResponse: { id : number; email : string; role : string; };
    DailyReportOut: { date : string; pnl_usd : number; spread_capture_pct : number | null; };
    DataSourceEntry: { component : string; status : string; source : string; detail : string; };
    DataSourceStatus: { checked_at : string; sim_ts : string; summary : string; sources : Array<components["schemas"]["DataSourceEntry"]>; };
    EndowmentIn: { battery?: components["schemas"]["BatteryIn"] | null; solar_mw?: number; cash_usd?: number; };
    EquityPoint: { tick_no : number; sim_ts : string; wall_ts : string; pnl_usd : string; soc_frac : number; };
    EvaluationRunOut: { id : number; status : string; rules_version : string; score : number | null; created_at : string; started_at : string | null; finished_at : string | null; seed_runs : Array<components["schemas"]["EvaluationSeedRunOut"]>; };
    EvaluationSeedRunOut: { seed_label : string; attempt : number; status : string; score : number | null; };
    ExternalMarketOut: { region : string; node : string; raw_lmp : string; p2p_anchor_price : string; import_price : string; export_price : string; interval_start : string | null; interval_end : string | null; currency : string; unit : string; status : string; source : string; detail : string; fetched_at : string; };
    ExternalTradeEvent: { kind?: "external.trade"; sim_ts : string; wall_ts : string; external_trade_id : number; vpp_id : number; side : string; price : string; raw_lmp : string; qty : string; region : string; node : string; counterparty?: string; interval_start?: string | null; interval_end?: string | null; };
    ForecastSkillMetric: { n : number; mae : number | null; bias : number | null; persistence_mae : number | null; skill_vs_persistence : number | null; };
    ForecastSkillResponse: { as_of : string; persistence_baseline : string; windows : { [key: string]: { [key: string]: { [key: string]: components["schemas"]["ForecastSkillMetric"]; }; }; }; };
    GuidanceIn: { preferred_modes?: Array<string>; avoid_modes?: Array<string>; mode_pin?: string | null; risk_budget?: number; price_bias_bps?: number; soc_target?: number; execution_style?: string; lesson?: string; meta_control?: { [key: string]: number; } | null; };
    GuidanceOut: { managed_id : number; guidance_source : string; applied : components["schemas"]["ReflectionEntryOut"]; applied_at : string; };
    HTTPValidationError: { detail?: Array<components["schemas"]["ValidationError"]>; };
    HistoryOut: { identity : string; session_id : number; points : Array<components["schemas"]["EquityPoint"]>; };
    LLMHealthOut: { ok_count : number; fail_count : number; last_ok_ts : string | null; state : string; };
    LeaderboardEntryOut: { rank : number; submission_id : number; user_email : string; algorithm : string; score : number; seed_ok_count : number; seed_failed_count : number; };
    LeaderboardRow: { identity : string; name : string; managed_def_id : number | null; category : string; strategy : string; is_llm : boolean; llm_model : string | null; pnl_usd : string; score : number; trade_count : number; energy_bought_kwh : number; energy_sold_kwh : number; soc_frac : number; sessions_count : number; hours : number; last_seen_at : string; };
    MagicLinkRequest: { email : string; };
    MagicLinkResponse: { sent : boolean; dev_token?: string | null; };
    ManagedSubmissionPayload: { algorithm : string; llm_enabled : boolean; preset?: string | null; endowment?: { [key: string]: unknown; } | null; risk?: unknown | null; };
    ManagedTradeOut: { trade_id : number | string; kind?: string | null; side : string; price : string; raw_lmp?: string | null; qty : string; cash : string; counterparty?: string | null; counterparty_vpp_id : number; buy_vpp_id : number; sell_vpp_id : number; sim_ts : string; wall_ts : string; };
    ManagedVPPCreate: { name : string; params?: { [key: string]: unknown; }; algorithm?: "ppo" | "truthful" | "zip" | "gd" | "aa"; llm_enabled?: boolean; online_learning?: boolean; persona?: string | null; agent_params?: { [key: string]: unknown; }; seed?: number | null; model?: string | null; };
    ManagedVPPOut: { id : number; vpp_id : number; name : string; params : { [key: string]: number | number | string | null; }; is_active : boolean; is_external : boolean; algorithm?: string; llm_enabled?: boolean; agent_kind : string; strategy : string; llm_live : boolean; llm_status : string; llm_health_state : string; persona?: string | null; model?: string | null; guidance_source?: string; chat_style?: string | null; chat_color?: string | null; chat_avatar?: string | null; };
    ManagedVPPPerformanceOut: { id : number; name : string; pnl : string; cumulative_energy_bought_kwh : number; cumulative_energy_sold_kwh : number; imbalance_unserved_load_kwh?: number; imbalance_spilled_generation_kwh?: number; imbalance_settlement_cash?: string; soc_kwh : number; soc_frac : number; recent_trades : Array<components["schemas"]["ManagedTradeOut"]>; reflections : Array<components["schemas"]["ReflectionEntryOut"]>; llm_health : components["schemas"]["LLMHealthOut"] | null; };
    ManagedVPPUpdate: { params?: { [key: string]: unknown; } | null; persona?: string | null; agent_params?: { [key: string]: unknown; } | null; model?: string | null; };
    MarketBalanceOut: { renewable_kw : number; load_kw : number; gas_capacity_kw : number; net_kw : number; supply_demand_ratio : number | null; bid_depth_kwh : number; ask_depth_kwh : number; };
    MarketReflectionOut: { vpp_id : number; vpp_name : string; health_state : string; ts : string; ok : boolean; price_adjust?: number | null; qty_scale?: number | null; preferred_modes?: Array<string> | null; avoid_modes?: Array<string> | null; mode_pin?: string | null; risk_budget?: number | null; price_bias_bps?: number | null; soc_target?: number | null; execution_style?: string | null; rationale?: string; meta_control?: { [key: string]: number; } | null; error : string | null; };
    MarketSessionOut: { market_mode : string; sim_time : string; wall_time : string; };
    MarketSnapshot: { sim_ts : string; speed : number; best_bid : string | null; best_ask : string | null; last_price : string | null; bids : Array<Array<unknown>>; asks : Array<Array<unknown>>; num_builtin_vpps : number; data_provenance : "real" | "cached" | "synthetic"; session : components["schemas"]["MarketSessionOut"]; data_source : components["schemas"]["DataSourceStatus"]; external_market : components["schemas"]["ExternalMarketOut"]; balance : components["schemas"]["MarketBalanceOut"]; };
    ModelsOut: { models : Array<string>; default : string; };
    OpenOrderOut: { order_id : number; vpp_id : number; side : string; price : string; remaining_qty : string; expires_at_sim?: string | null; };
    OrderBatch: { protocol_version?: number; idempotency_key?: string | null; deadline?: string | null; orders?: Array<components["schemas"]["BatchOrderItem"]>; cancels?: Array<number>; };
    OrderCancel: { order_id : number; };
    OrderSubmit: { vpp_id : number; side : "buy" | "sell"; price : number | string; qty : number | string; };
    OrderSubmitResponse: { order_id : number; remaining_qty : string; expires_at_sim?: string | null; trades : Array<components["schemas"]["TradeEvent"] | components["schemas"]["ExternalTradeEvent"]>; };
    ParticipantOut: { id : number; name : string; kind : string; strategy?: string | null; };
    PpoRenewStartOut: { state : string; started_at : string | null; finished_at : string | null; detail : string; reloaded : number; error : string | null; metrics : { [key: string]: unknown; } | null; status : string; };
    PpoRenewStatusOut: { state : string; started_at : string | null; finished_at : string | null; detail : string; reloaded : number; error : string | null; metrics : { [key: string]: unknown; } | null; };
    ProveOutCreateIn: { label?: string | null; endowment : components["schemas"]["EndowmentIn"]; window : components["schemas"]["WindowIn"]; strategy?: components["schemas"]["StrategyIn"]; };
    ProveOutDetailOut: { run_id : number; label : string | null; status : string; endowment : { [key: string]: unknown; }; window_start : string; window_end : string; strategy : { [key: string]: unknown; }; report : components["schemas"]["ProveOutReportOut"] | null; error : string | null; created_at : string; finished_at : string | null; };
    ProveOutListOut: { run_id : number; label : string | null; status : string; window_start : string; window_end : string; created_at : string; pnl_usd?: number | null; spread_capture_pct?: number | null; };
    ProveOutQueuedOut: { run_id : number; status : string; };
    ProveOutReportOut: { pnl_usd : number; per_kw_month : number; spread_capture_pct : number | null; perfect_foresight_usd : number; baseline_hold_usd : number; max_drawdown_usd : number; trades : number; risk_rejections : number; imbalance_penalty_usd : number; days : number; daily : Array<components["schemas"]["DailyReportOut"]>; };
    ReflectionEntryOut: { ts : string; ok : boolean; price_adjust?: number | null; qty_scale?: number | null; preferred_modes?: Array<string> | null; avoid_modes?: Array<string> | null; mode_pin?: string | null; risk_budget?: number | null; price_bias_bps?: number | null; soc_target?: number | null; execution_style?: string | null; rationale?: string; lesson?: string | null; meta_control?: { [key: string]: number; } | null; error : string | null; };
    SayIn: { text : string; };
    SessionOut: { id : number; market_mode : string; started_at : string; ended_at : string | null; price_ref : string; is_current : boolean; };
    SessionResponse: { session_token : string; user_id : number; email : string; };
    SpeedStatusOut: { speed : number; is_realtime : boolean; };
    SpeedUpdate: { speed : number; };
    StrategyIn: { algorithm?: string; params?: { [key: string]: unknown; } | null; };
    SubmissionCreateIn: { track : "managed"; payload : components["schemas"]["ManagedSubmissionPayload"]; };
    SubmissionDetailOut: { id : number; competition_id : number; track : string; status : string; payload : { [key: string]: unknown; }; created_at : string; updated_at : string; latest_run : components["schemas"]["EvaluationRunOut"] | null; };
    SubmissionOut: { id : number; competition_id : number; track : string; status : string; payload : { [key: string]: unknown; }; created_at : string; updated_at : string; };
    SupplyCurveOrder: { price : string; qty : string; category : string; vpp_name : string | null; };
    SupplyCurveOut: { sim_ts : string; asks : Array<components["schemas"]["SupplyCurveOrder"]>; bids : Array<components["schemas"]["SupplyCurveOrder"]>; };
    TradeEvent: { kind?: "trade"; sim_ts : string; wall_ts : string; trade_id : number; buy_order_id : number; sell_order_id : number; buy_vpp_id : number; sell_vpp_id : number; price : string; qty : string; };
    VPPCreate: { name : string; params?: { [key: string]: unknown; }; };
    VPPOut: { id : number; name : string; params : { [key: string]: number | number | string | null; }; is_active : boolean; is_external : boolean; created_at : string; };
    ValidationError: { loc : Array<string | number>; msg : string; type : string; input?: unknown; ctx?: Record<string, unknown>; };
    WindowIn: { start_date : string; end_date : string; };
    eflux__api__routers__competitions__LeaderboardOut: { competition_slug : string; entries : Array<components["schemas"]["LeaderboardEntryOut"]>; };
    eflux__api__routers__leaderboard__LeaderboardOut: { scope : "session" | "alltime"; session_id : number | null; market_mode : string; rows : Array<components["schemas"]["LeaderboardRow"]>; };
  };
}

export type schemas = components["schemas"];
