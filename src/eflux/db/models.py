"""SQLAlchemy ORM models for users, VPPs, orders, trades, and market-result durability."""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from eflux.db.base import Base, utcnow


class OrderSide(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(enum.StrEnum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(
        String(10), default="user", server_default="user", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    vpps: Mapped[list[VPP]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    submissions: Mapped[list[Submission]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    prove_out_runs: Mapped[list[ProveOutRun]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(back_populates="actor_user")


class MagicLink(Base):
    __tablename__ = "magic_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="api_keys")


class VPP(Base):
    __tablename__ = "vpps"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_vpp_owner_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Free-form parameters for DER mix:
    #   {"pv_kw": 10.0, "battery_kwh": 20.0, "battery_kw": 5.0,
    #    "load_kw_base": 3.0, "load_elasticity": 0.2, ...}
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_external: Mapped[bool] = mapped_column(default=False, nullable=False)  # external SDK vs built-in
    # Platform-driven managed agent (Tier 0 of docs/EXTERNAL_PARTICIPATION.md): the simulator
    # runs a HybridPolicyAgent for the user. managed_config carries the non-DER bits needed to
    # re-instantiate it on restart: {"persona": str|None, "agent_params": {...}, "seed": int|None}.
    is_managed: Mapped[bool] = mapped_column(default=False, nullable=False)
    managed_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="vpps")
    orders: Mapped[list[Order]] = relationship(back_populates="vpp")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_vpp_status", "vpp_id", "status"),
        Index("ix_orders_sim_ts", "sim_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vpp_id: Mapped[int] = mapped_column(
        ForeignKey("vpps.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide), nullable=False)
    # Price in currency units per MWh (or chosen unit). Use Numeric for precision.
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    remaining_qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.OPEN, nullable=False
    )
    sim_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wall_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    vpp: Mapped[VPP] = relationship(back_populates="orders")


class MarketSession(Base):
    """One row per backend boot — the durable identity of an otherwise ephemeral market.

    Live market state (orders, trades, PnL) is in-memory by design; snapshots reference
    a session so leaderboard results survive restarts and stay comparable (same
    market_mode + price_ref) across runs. ended_at is set on clean shutdown and stays
    NULL for the running (or crashed) session.
    """

    __tablename__ = "market_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The run's cost-basis anchor ($/MWh) — also the normalized score's denominator.
    price_ref: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    scenario_file: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    scenario_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    market_speed: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    tick_sim_sec: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    snapshots: Mapped[list[VppStatSnapshot]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class VppStatSnapshot(Base):
    """Periodic per-agent stat sample — the durable substrate of the leaderboard.

    Identity across restarts: built-in roster agents are keyed by ``name`` (stable via the
    scenario file); user-provisioned managed agents by ``managed_def_id`` (their DB row id).
    The runtime ``vpp_id`` is informational only (negative, reassigned every boot).
    Endowment fields are denormalized per row so scoring never joins back to live params.
    """

    __tablename__ = "vpp_stat_snapshots"
    __table_args__ = (
        Index("ix_snap_session_identity", "session_id", "name", "id"),
        Index("ix_snap_session_wall", "session_id", "wall_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("market_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vpp_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    managed_def_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    is_llm: Mapped[bool] = mapped_column(default=False, nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tick_no: Mapped[int] = mapped_column(Integer, nullable=False)
    sim_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wall_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    pnl_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    soc_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    soc_frac: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_bought_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_sold_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pv_kw_peak: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    wind_kw_rated: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    battery_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    battery_kw_max: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    load_kw_base: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gas_kw_max: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    session: Mapped[MarketSession] = relationship(back_populates="snapshots")


class ForecastOutcome(Base):
    """One issued forecast point, updated when its target becomes realized."""

    __tablename__ = "forecast_outcomes"
    __table_args__ = (
        UniqueConstraint("origin_ts", "horizon", "market", name="uq_forecast_outcome_origin"),
        Index("ix_forecast_outcome_market_target", "market", "target_ts"),
        Index("ix_forecast_outcome_origin", "origin_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    anchor_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    residual: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted: Mapped[float] = mapped_column(Float, nullable=False)
    realized: Mapped[float | None] = mapped_column(Float, nullable=True)
    provenance: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    rulesets: Mapped[list[CompetitionRuleSet]] = relationship(
        back_populates="competition", cascade="all, delete-orphan", passive_deletes=True
    )
    submissions: Mapped[list[Submission]] = relationship(
        back_populates="competition", cascade="all, delete-orphan", passive_deletes=True
    )


class CompetitionRuleSet(Base):
    __tablename__ = "competition_rulesets"
    __table_args__ = (
        UniqueConstraint("competition_id", "track", "version", name="uq_competition_track_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    track: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    competition: Mapped[Competition] = relationship(back_populates="rulesets")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    competition: Mapped[Competition] = relationship(back_populates="submissions")
    user: Mapped[User] = relationship(back_populates="submissions")
    evaluation_runs: Mapped[list[EvaluationRun]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", passive_deletes=True
    )


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    submission: Mapped[Submission] = relationship(back_populates="evaluation_runs")
    seed_runs: Mapped[list[EvaluationSeedRun]] = relationship(
        back_populates="evaluation_run", cascade="all, delete-orphan", passive_deletes=True
    )
    metrics: Mapped[list[EvaluationMetric]] = relationship(
        back_populates="evaluation_run", cascade="all, delete-orphan", passive_deletes=True
    )


class EvaluationSeedRun(Base):
    __tablename__ = "evaluation_seed_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seed_label: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    evaluation_run: Mapped[EvaluationRun] = relationship(back_populates="seed_runs")


class EvaluationMetric(Base):
    __tablename__ = "evaluation_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seed_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    evaluation_run: Mapped[EvaluationRun] = relationship(back_populates="metrics")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    actor_user: Mapped[User | None] = relationship(back_populates="audit_events")


class ProveOutRun(Base):
    """Private historical replay requested by one trader."""

    __tablename__ = "prove_out_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name="ck_prove_out_runs_status",
        ),
        Index("ix_prove_out_runs_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    endowment: Mapped[dict] = mapped_column(JSON, nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    strategy: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="prove_out_runs")


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_sim_ts", "sim_ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    buy_order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    sell_order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    buy_vpp_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sell_vpp_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    sim_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wall_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
