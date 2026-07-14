"""add job leases and deployment mode evidence isolation

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_TABLES = (
    "evaluation_runs",
    "prove_out_runs",
    "release_evaluations",
    "dataset_training_runs",
)


def _column_names(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in _JOB_TABLES:
        columns = _column_names(table)
        if "claimed_at" not in columns:
            op.add_column(
                table, sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
            )
        if "lease_expires_at" not in columns:
            op.add_column(
                table,
                sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            )
    if "deployment_mode" not in _column_names("vpp_stat_snapshots"):
        op.add_column(
            "vpp_stat_snapshots",
            sa.Column(
                "deployment_mode",
                sa.String(length=16),
                server_default="unknown",
                nullable=False,
            ),
        )


def downgrade() -> None:
    if "deployment_mode" in _column_names("vpp_stat_snapshots"):
        op.drop_column("vpp_stat_snapshots", "deployment_mode")
    for table in reversed(_JOB_TABLES):
        columns = _column_names(table)
        if "lease_expires_at" in columns:
            op.drop_column(table, "lease_expires_at")
        if "claimed_at" in columns:
            op.drop_column(table, "claimed_at")
