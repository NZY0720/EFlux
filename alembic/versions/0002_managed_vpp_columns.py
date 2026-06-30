"""add managed-agent columns to vpps (Tier 0 external participation)

Adds ``is_managed`` and ``managed_config`` so a user-provisioned, platform-hosted
managed agent (a HybridPolicyAgent the simulator drives) can be persisted and
re-provisioned on restart. See docs/EXTERNAL_PARTICIPATION.md.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vpps",
        sa.Column("is_managed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("vpps", sa.Column("managed_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vpps") as batch:
        batch.drop_column("managed_config")
        batch.drop_column("is_managed")
