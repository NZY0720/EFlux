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
    # Resting orders expire after this many sim-seconds (0 disables expiry).
    # Keeps never-crossing quotes (e.g. gas asks above every bid) from piling
    # up in the book. Must exceed the agents' 30-tick quote cadence or the
    # book flickers empty between re-quotes.
    order_ttl_sec: float = 180.0
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

    # --- Durable results (leaderboard) --------------------------------------------------
    # Periodic per-agent stat snapshots to the DB so PnL/leaderboard survive restarts.
    stats_enabled: bool = True
    # Wall-clock seconds between snapshot batches. Wall-gated (not tick-modulo) so
    # running the market at 10x/100x doesn't multiply the write volume.
    stats_snapshot_sec: float = 30.0
    # Snapshots older than this are pruned at startup (0 disables pruning).
    stats_retention_days: int = 14
    # Where the backtest runner writes run artifacts (manifest, metrics CSVs, charts);
    # the /benchmarks API serves them read-only. Relative paths resolve to PROJECT_ROOT.
    backtest_artifacts_dir: str = "artifacts/backtests"

    llm_provider: str = "opencode"
    llm_key_file: str = "key.txt"
    llm_base_url: str = ""
    llm_model: str = "deepseek-v4-pro"
    # Reasoning models can take >30s per completion; the old 30s default made
    # most reflections die with ReadTimeout.
    llm_timeout_sec: float = 120.0
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
    def llm_api_key(self) -> str | None:
        path = PROJECT_ROOT / self.llm_key_file
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
