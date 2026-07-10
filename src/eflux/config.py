"""Application settings — loaded from config.env + environment + key.txt."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / "config.env"),
        env_prefix="EFLUX_",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = "dev"
    log_level: str = "INFO"
    secret_key: str = "dev-only-change-me"

    db_url: str = "sqlite+aiosqlite:///./eflux_dev.db"
    # Dev convenience: have lifespan run `Base.metadata.create_all` so a fresh
    # SQLite file Just Works without `alembic upgrade head`. Set to False once
    # you start managing schema via migrations exclusively (production).
    auto_create_schema: bool = True

    redis_url: str = "redis://localhost:6379/0"
    # Event-bus backend. "memory" = in-process fan-out (default, no extra deps).
    # "redis" = RedisStreamBus on redis_url; lifespan pings on startup and falls
    # back to "memory" if Redis is unreachable.
    bus_backend: Literal["memory", "redis"] = "memory"

    market_speed: float = 1.0
    market_tick_sec: float = 1.0
    agent_decision_interval_sec: float = 30.0
    delivery_interval_sec: int = 300
    delivery_horizon_intervals: int = 6
    # Agent requests may override this; tactical policies default to one
    # decision interval so stale exposure is released before the next quote.
    order_ttl_sec: float = 30.0
    site_timezone: str = "America/Los_Angeles"
    market_region: str = "caiso_sp15"
    # Which market this process runs (one market per launch — see the two .command
    # launchers). "p2p" = peer-to-peer continuous double auction; CAISO is a
    # reference line only and never anchors agent prices. "realprice" = pure
    # price-taking against the live CAISO price (every order settles vs the grid at
    # import/export; agents never trade each other and don't move the price).
    market_mode: Literal["p2p", "realprice"] = "p2p"
    external_market_enabled: bool = True
    external_market_poll_sec: float = 60.0
    external_market_node: str = "TH_SP15_GEN-APND"
    external_market_fallback_price: float = 50.0
    external_market_transaction_fee: float = 2.0
    # Keep the real-price venue operational on the clearly labelled synthetic
    # fallback while CAISO is temporarily unavailable.  Disabling the external
    # market entirely still disables grid liquidity.
    realprice_fallback_trading_enabled: bool = True
    imbalance_settlement_enabled: bool = True
    imbalance_penalty_mult: float = 2.0
    curtailment_price_per_mwh: float = 0.0
    forecast_enabled: bool = True
    forecast_refresh_sec: float = 60.0
    forecast_warmup_days: int = 30
    forecast_state_dir: str = "checkpoints/forecast"
    # CAISO 429 throttling can degrade the warm-up fetch to partial/empty price
    # data (and the thin result is cached for the rest of the day); below this
    # many price points the bootstrap falls back to the newest cached window.
    forecast_warmup_min_price_points: int = 168
    # A warm-start window must be continuous enough for lag/trend features to be
    # meaningful. Partial CAISO responses often have week-long holes even when
    # their raw point count clears the old minimum.
    forecast_warmup_max_gap_hours: float = 2.0
    forecast_bootstrap_timeout_sec: float = 120.0
    # Anchor price forecasts to the published CAISO DAM day-ahead hourly curve
    # (same hybrid design as weather NWP). Off ⇒ plain autoregressive models.
    forecast_dam_anchor_enabled: bool = True
    # Keep price forecasts physically/plausibly bounded without erasing the
    # legitimate negative CAISO price regime.
    forecast_price_min: float = -50.0
    forecast_price_max: float = 250.0
    # DAM hybrid calibration. The anchor receives increasing weight with lead
    # time; the learned real-time spread decays away at longer horizons.
    forecast_dam_residual_limit: float = 20.0
    forecast_dam_blend_max: float = 0.85
    forecast_dam_blend_full_hours: float = 6.0
    forecast_dam_residual_decay_hours: float = 4.0
    # P2P price forecasts anchor on the market's own hourly clearing profile —
    # never CAISO DAM, which tracks grid settlement and runs structurally above
    # P2P clearing (the learned residual then over-corrects toward zero).
    forecast_p2p_profile_alpha: float = 0.05
    forecast_p2p_profile_min_obs: int = 5
    # A cold own-market profile is honestly FLAT, which removes the forecast
    # spread the eta-guarded arbitrage needs — no trades ⇒ no profile data ⇒
    # still flat (2026-07-11 stall). Seed cold buckets with the warm-up CAISO
    # intraday SHAPE rescaled to the P2P price level; real prints replace it.
    forecast_p2p_profile_prior_enabled: bool = True
    # Keep the CAISO quote fresh even in P2P mode so price_real has realized
    # observations and can learn a DAM-vs-RTM spread.
    forecast_poll_realprice_in_p2p: bool = True
    # Durable scored forecast records are pruned on every bounded write.
    forecast_outcome_retention_days: int = 14
    # Start each backend session with a CLEAN hub chart: don't restore the
    # history deque / last bundle from state.json and don't backfill /history
    # from the DB. Models stay warm either way; /forecasts/skill stays durable.
    forecast_history_reset_on_boot: bool = True
    # Derive an endowment-driven Character for live strategy/hybrid/managed agents.
    agent_character_enabled: bool = True
    site_default_lat: float = 34.05
    site_default_lon: float = -118.25
    site_wind_lat: float = 33.90
    site_wind_lon: float = -116.60
    # Built-in VPP roster (relative paths resolve against the project root).
    scenario_file: str = "scenarios/default.yaml"
    # Cost diversification: spread each non-LLM agent's price_ref by ±this
    # fraction (deterministic per agent) so battery-band asks (price_ref/√eta)
    # and deficit bids don't all collapse onto one price level — otherwise the
    # market clears at ~2 discrete prints and the price chart is a square wave.
    # 0 disables (every agent uses the default 50). LLM/reflective agents are
    # always excluded. Gas is unaffected (its cost is set per-VPP already).
    price_ref_jitter_frac: float = 0.06
    # What the cost-basis reference (price_ref) and the PPO normalization scale are
    # calibrated to. "static" = the fixed legacy 50 $/MWh. "caiso" = the trailing-month
    # CAISO LMP mean (a *fixed* value computed once per run, never the live tick — see
    # data/caiso_reference.py). The live config.env sets this to "caiso"; the default stays
    # "static" so library/test use needs no network and stays deterministic.
    price_ref_source: Literal["static", "caiso"] = "static"
    # Trailing window (days) for the CAISO reference mean when price_ref_source="caiso".
    price_ref_window_days: int = 30

    magic_link_ttl_min: int = 15
    session_ttl_day: int = 30
    api_key_prefix: str = "eflux_"
    admin_emails: str = ""

    # --- Durable results (leaderboard) --------------------------------------------------
    # Periodic per-agent stat snapshots to the DB so PnL/leaderboard survive restarts.
    stats_enabled: bool = True
    # Wall-clock seconds between snapshot batches. Wall-gated (not tick-modulo) so
    # running the market at 10x/100x doesn't multiply the write volume.
    stats_snapshot_sec: float = 30.0
    # Snapshots older than this are pruned at startup (0 disables pruning).
    stats_retention_days: int = 14
    # Arena comparisons stay hidden until both sides have enough actual market
    # participation and simulated observation time to support a meaningful claim.
    arena_min_trades: int = 10
    arena_min_observation_min: int = 30
    # Where the backtest runner writes run artifacts (manifest, metrics CSVs, charts);
    # the /benchmarks API serves them read-only. Relative paths resolve to PROJECT_ROOT.
    backtest_artifacts_dir: str = "artifacts/backtests"
    # Dedicated official-evaluation worker queue poll cadence (wall seconds).
    evaluation_poll_sec: float = 5.0
    # `tasks.sh run` starts a local queue worker unless this is explicitly disabled.
    evaluation_worker_autostart: bool = True

    llm_provider: str = "opencode"
    llm_key_file: str = "key.txt"
    llm_base_url: str = ""
    llm_model: str = "deepseek-v4-pro"
    # Reasoning models can take >30s per completion; the old 30s default made
    # most reflections die with ReadTimeout.
    llm_timeout_sec: float = 120.0
    # Shared hard ceiling across strategist/chat/model clients.  Rates are an
    # explicit conservative estimate because OpenAI-compatible providers expose
    # heterogeneous model catalogues and billing.
    llm_budget_usd: float = 10.0
    llm_input_cost_per_million_tokens: float = 3.0
    llm_output_cost_per_million_tokens: float = 15.0
    # LLM-managed hybrid agents. Off by default so no key/base_url is needed for
    # default dev runs. The EFLUX_REFLECTIVE_* env names are kept for compatibility.
    reflective_enabled: bool = False
    reflective_interval_ticks: int = 60

    # --- Online PPO (live learning) ----------------------------------------------------
    # Global kill-switch for the custom online PPO learner. When False, a `ppo_online`
    # executor is built frozen (serve-only, no live updates) — useful to A/B a learning vs
    # static policy without editing the roster.
    online_learning_enabled: bool = True
    # Run the hybrid agent's PPO update off the tick path (worker thread) instead of
    # synchronously inline. Inline (default) is the conservative, deterministic path; the
    # net is tiny so an inline update is sub-millisecond.
    online_update_async: bool = False
    # If set, live-updated online policy weights are saved here (one file per VPP) on
    # shutdown, so a session resumes from where learning left off. Empty disables it.
    online_learning_save_dir: str = ""
    # PPO structured-action encoding for fresh online policies. Checkpoints still infer
    # their own encoding from weight shapes so legacy V1 files load unchanged.
    ppo_encoding_version: int = 2
    # Warm-start checkpoint used for API-provisioned managed hybrid/PPO agents. Missing
    # files already fall back to a fresh online policy with a warning in the executor builder.
    managed_ppo_checkpoint: str = "checkpoints/bc_primitive_p2p_v4.pt"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator("market_speed")
    @classmethod
    def _validate_speed(cls, v: float) -> float:
        if v not in (1.0, 10.0, 100.0):
            raise ValueError("market_speed must be 1.0, 10.0, or 100.0")
        return v

    @field_validator("llm_base_url", "llm_model", mode="before")
    @classmethod
    def _blank_inline_comment_placeholders(cls, v: str | None) -> str:
        if v is None:
            return ""
        text = str(v).strip()
        return "" if text.startswith("#") else text

    @property
    def is_realtime(self) -> bool:
        return self.market_speed == 1.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_email_set(self) -> set[str]:
        return {email.strip().lower() for email in self.admin_emails.split(",") if email.strip()}

    @property
    def llm_api_key(self) -> str | None:
        path = PROJECT_ROOT / self.llm_key_file
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
