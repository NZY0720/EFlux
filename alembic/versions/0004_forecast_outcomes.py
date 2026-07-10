"""add durable forecast outcomes

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "forecast_outcomes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("origin_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon", sa.String(length=8), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("anchor_value", sa.Float(), nullable=True),
        sa.Column("residual", sa.Float(), nullable=True),
        sa.Column("predicted", sa.Float(), nullable=False),
        sa.Column("realized", sa.Float(), nullable=True),
        sa.Column("provenance", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("origin_ts", "horizon", "market", name="uq_forecast_outcome_origin"),
    )
    op.create_index("ix_forecast_outcome_market_target", "forecast_outcomes", ["market", "target_ts"])
    op.create_index("ix_forecast_outcome_origin", "forecast_outcomes", ["origin_ts"])


def downgrade() -> None:
    op.drop_index("ix_forecast_outcome_origin", table_name="forecast_outcomes")
    op.drop_index("ix_forecast_outcome_market_target", table_name="forecast_outcomes")
    op.drop_table("forecast_outcomes")
