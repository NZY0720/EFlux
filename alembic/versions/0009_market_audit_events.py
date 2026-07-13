"""persist the append-only market audit stream

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("market_audit_events"):
        op.create_table(
            "market_audit_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("sequence_no", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=48), nullable=False),
            sa.Column("interval_id", sa.String(length=160)),
            sa.Column("participant_id", sa.Integer()),
            sa.Column("reference_id", sa.String(length=128)),
            sa.Column("sim_ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("wall_ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["market_sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("session_id", "sequence_no", name="uq_market_audit_sequence"),
        )

    # Older dev startup code could create this table via metadata.create_all
    # before Alembic reached 0009. Reconcile that valid table instead of failing
    # with "already exists", while still ensuring the migration-owned indexes.
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("market_audit_events")}
    for name, columns in (
        ("ix_market_audit_events_session_id", ["session_id"]),
        ("ix_market_audit_interval", ["session_id", "interval_id", "sequence_no"]),
        ("ix_market_audit_participant", ["session_id", "participant_id", "sequence_no"]),
    ):
        if name not in indexes:
            op.create_index(name, "market_audit_events", columns)


def downgrade() -> None:
    op.drop_index("ix_market_audit_participant", table_name="market_audit_events")
    op.drop_index("ix_market_audit_interval", table_name="market_audit_events")
    op.drop_index("ix_market_audit_events_session_id", table_name="market_audit_events")
    op.drop_table("market_audit_events")
