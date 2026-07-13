"""add immutable evaluation evidence and final holdout state

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("competitions", sa.Column("closed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "submissions",
        sa.Column("selected_for_final", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "submissions", sa.Column("selected_for_final_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="hidden"),
    )
    op.add_column("evaluation_runs", sa.Column("manifest", sa.JSON()))
    op.add_column(
        "evaluation_runs", sa.Column("manifest_sha256", sa.String(length=64))
    )
    op.add_column("evaluation_runs", sa.Column("evidence", sa.JSON()))
    op.add_column(
        "evaluation_runs", sa.Column("evidence_sha256", sa.String(length=64))
    )
    op.add_column("prove_out_runs", sa.Column("manifest", sa.JSON()))
    op.add_column(
        "prove_out_runs", sa.Column("manifest_sha256", sa.String(length=64))
    )
    op.add_column("prove_out_runs", sa.Column("evidence", sa.JSON()))
    op.add_column(
        "prove_out_runs", sa.Column("evidence_sha256", sa.String(length=64))
    )


def downgrade() -> None:
    op.drop_column("prove_out_runs", "evidence_sha256")
    op.drop_column("prove_out_runs", "evidence")
    op.drop_column("prove_out_runs", "manifest_sha256")
    op.drop_column("prove_out_runs", "manifest")
    op.drop_column("evaluation_runs", "evidence_sha256")
    op.drop_column("evaluation_runs", "evidence")
    op.drop_column("evaluation_runs", "manifest_sha256")
    op.drop_column("evaluation_runs", "manifest")
    op.drop_column("evaluation_runs", "kind")
    op.drop_column("submissions", "selected_for_final_at")
    op.drop_column("submissions", "selected_for_final")
    op.drop_column("competitions", "closed_at")
