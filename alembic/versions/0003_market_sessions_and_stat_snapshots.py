"""add market_sessions + vpp_stat_snapshots (durable leaderboard results)

Live market state is in-memory and wiped on restart. These tables give results a
durable identity: one market_sessions row per backend boot, plus periodic per-agent
vpp_stat_snapshots samples, so the leaderboard (and per-agent equity history)
survives restarts. See docs/EXTERNAL_PARTICIPATION.md §6.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market_mode", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_ref", sa.Numeric(12, 4), nullable=False),
        sa.Column("scenario_file", sa.String(length=255), nullable=False),
        sa.Column("scenario_sha256", sa.String(length=64), nullable=True),
        sa.Column("market_speed", sa.Float(), nullable=False),
        sa.Column("tick_sim_sec", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "vpp_stat_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("vpp_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("managed_def_id", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("is_llm", sa.Boolean(), nullable=False),
        sa.Column("llm_model", sa.String(length=64), nullable=True),
        sa.Column("tick_no", sa.Integer(), nullable=False),
        sa.Column("sim_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wall_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pnl_usd", sa.Numeric(14, 4), nullable=False),
        sa.Column("soc_kwh", sa.Float(), nullable=False),
        sa.Column("soc_frac", sa.Float(), nullable=False),
        sa.Column("energy_bought_kwh", sa.Float(), nullable=False),
        sa.Column("energy_sold_kwh", sa.Float(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("pv_kw_peak", sa.Float(), nullable=False),
        sa.Column("wind_kw_rated", sa.Float(), nullable=False),
        sa.Column("battery_kwh", sa.Float(), nullable=False),
        sa.Column("battery_kw_max", sa.Float(), nullable=False),
        sa.Column("load_kw_base", sa.Float(), nullable=False),
        sa.Column("gas_kw_max", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["market_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vpp_stat_snapshots_session_id", "vpp_stat_snapshots", ["session_id"])
    op.create_index("ix_vpp_stat_snapshots_name", "vpp_stat_snapshots", ["name"])
    op.create_index(
        "ix_vpp_stat_snapshots_managed_def_id", "vpp_stat_snapshots", ["managed_def_id"]
    )
    op.create_index(
        "ix_snap_session_identity", "vpp_stat_snapshots", ["session_id", "name", "id"]
    )
    op.create_index("ix_snap_session_wall", "vpp_stat_snapshots", ["session_id", "wall_ts"])


def downgrade() -> None:
    op.drop_index("ix_snap_session_wall", table_name="vpp_stat_snapshots")
    op.drop_index("ix_snap_session_identity", table_name="vpp_stat_snapshots")
    op.drop_index("ix_vpp_stat_snapshots_managed_def_id", table_name="vpp_stat_snapshots")
    op.drop_index("ix_vpp_stat_snapshots_name", table_name="vpp_stat_snapshots")
    op.drop_index("ix_vpp_stat_snapshots_session_id", table_name="vpp_stat_snapshots")
    op.drop_table("vpp_stat_snapshots")
    op.drop_table("market_sessions")
