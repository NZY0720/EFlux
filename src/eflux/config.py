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
    site_timezone: str = "Asia/Hong_Kong"
    # Built-in VPP roster (relative paths resolve against the project root).
    scenario_file: str = "scenarios/default.yaml"
    # Cost diversification: spread each non-LLM agent's price_ref by ±this
    # fraction (deterministic per agent) so battery-band asks (price_ref/√eta)
    # and deficit bids don't all collapse onto one price level — otherwise the
    # market clears at ~2 discrete prints and the price chart is a square wave.
    # 0 disables (every agent uses the default 50). LLM/reflective agents are
    # always excluded. Gas is unaffected (its cost is set per-VPP already).
    price_ref_jitter_frac: float = 0.06

    magic_link_ttl_min: int = 15
    session_ttl_day: int = 30
    api_key_prefix: str = "eflux_"

    llm_provider: str = "xiaomi-mimo"
    llm_key_file: str = "key.txt"
    llm_base_url: str = ""
    llm_model: str = ""
    # Reasoning models (mimo-v2.5-pro) regularly take >30s per completion; the
    # old 30s default made most reflections die with ReadTimeout.
    llm_timeout_sec: float = 120.0
    # LLM-managed hybrid agents. Off by default so no key/base_url is needed for
    # default dev runs. Env name kept for compatibility with earlier ReflectiveAgent.
    reflective_enabled: bool = False
    reflective_interval_ticks: int = 60
    # Legacy ReflectiveAgent learning memory (JSONL). HybridPolicyAgent uses
    # in-memory guidance audit logs instead. Relative paths resolve against the project root.
    agent_memory_dir: str = "data/agent_memory"

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
