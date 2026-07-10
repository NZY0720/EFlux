"""add user roles

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=10), nullable=False, server_default="user"),
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("role")
