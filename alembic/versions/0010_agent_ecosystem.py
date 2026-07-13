"""add immutable agent releases and behavior-dataset ecosystem

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _column_names(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _index_names(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def _foreign_key_names(table: str) -> set[str | None]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_foreign_keys(table)}


def _ensure_index(name: str, table: str, columns: list[str]) -> None:
    if name not in _index_names(table):
        op.create_index(name, table, columns)


def upgrade() -> None:
    if not _has_table("agent_releases"):
        op.create_table(
            "agent_releases",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False),
            sa.Column("visibility", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("recipe", sa.JSON(), nullable=False),
            sa.Column("state", sa.JSON(), nullable=False),
            sa.Column("compatibility", sa.JSON(), nullable=False),
            sa.Column("environment", sa.JSON(), nullable=False),
            sa.Column("badges", sa.JSON(), nullable=False),
            sa.Column("parent_release_id", sa.Integer()),
            sa.Column("content_sha256", sa.String(length=64)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "market IN ('realprice', 'p2p', 'hybrid')", name="ck_agent_releases_market"
            ),
            sa.CheckConstraint(
                "status IN ('draft', 'published', 'verified')", name="ck_agent_releases_status"
            ),
            sa.CheckConstraint(
                "visibility IN ('public', 'private')", name="ck_agent_releases_visibility"
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["parent_release_id"], ["agent_releases.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "name", "version", name="uq_agent_release_version"),
        )
    for name, columns in (
        ("ix_agent_releases_owner_id", ["owner_id"]),
        ("ix_agent_releases_parent_release_id", ["parent_release_id"]),
        ("ix_agent_releases_content_sha256", ["content_sha256"]),
        ("ix_agent_releases_market_status", ["market", "status"]),
    ):
        _ensure_index(name, "agent_releases", columns)

    if not _has_table("release_evaluations"):
        op.create_table(
            "release_evaluations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("release_id", sa.Integer(), nullable=False),
            sa.Column("requested_by_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("provenance", sa.String(length=32), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("evidence", sa.JSON()),
            sa.Column("evidence_sha256", sa.String(length=64)),
            sa.Column("error", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "kind IN ('deterministic_replay', 'fresh_llm_replay', 'forward_shadow', "
                "'verified_live', 'p2p_tournament', 'hybrid_evaluation')",
                name="ck_release_evaluations_kind",
            ),
            sa.CheckConstraint(
                "provenance IN ('platform_verified', 'externally_attested', 'self_reported')",
                name="ck_release_evaluations_provenance",
            ),
            sa.CheckConstraint(
                "status IN ('queued', 'running', 'done', 'failed')",
                name="ck_release_evaluations_status",
            ),
            sa.ForeignKeyConstraint(["release_id"], ["agent_releases.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    for name, columns in (
        ("ix_release_evaluations_release_id", ["release_id"]),
        ("ix_release_evaluations_requested_by_id", ["requested_by_id"]),
        ("ix_release_eval_release_created", ["release_id", "created_at"]),
    ):
        _ensure_index(name, "release_evaluations", columns)

    if not _has_table("behavior_datasets"):
        op.create_table(
            "behavior_datasets",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False),
            sa.Column("visibility", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("schema_version", sa.String(length=32), nullable=False),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("artifact_path", sa.String(length=500)),
            sa.Column("artifact_sha256", sa.String(length=64)),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False),
            sa.Column("license", sa.String(length=100), nullable=False),
            sa.Column("parent_dataset_id", sa.Integer()),
            sa.Column("source_release_id", sa.Integer()),
            sa.Column("content_sha256", sa.String(length=64)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "market IN ('realprice', 'p2p', 'hybrid')", name="ck_behavior_datasets_market"
            ),
            sa.CheckConstraint(
                "status IN ('draft', 'published', 'verified')",
                name="ck_behavior_datasets_status",
            ),
            sa.CheckConstraint(
                "visibility IN ('public', 'private')", name="ck_behavior_datasets_visibility"
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["parent_dataset_id"], ["behavior_datasets.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["source_release_id"], ["agent_releases.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "name", "version", name="uq_behavior_dataset_version"),
        )
    for index, columns in (
        ("ix_behavior_datasets_owner_id", ["owner_id"]),
        ("ix_behavior_datasets_parent_dataset_id", ["parent_dataset_id"]),
        ("ix_behavior_datasets_source_release_id", ["source_release_id"]),
        ("ix_behavior_datasets_content_sha256", ["content_sha256"]),
        ("ix_behavior_datasets_market_status", ["market", "status"]),
    ):
        _ensure_index(index, "behavior_datasets", columns)

    if not _has_table("dataset_training_runs"):
        op.create_table(
            "dataset_training_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("dataset_id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("algorithm", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("output_release_id", sa.Integer()),
            sa.Column("error", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "algorithm IN ('bc_warm_start', 'ppo_finetune')",
                name="ck_dataset_training_algorithm",
            ),
            sa.CheckConstraint(
                "status IN ('queued', 'running', 'succeeded', 'failed')",
                name="ck_dataset_training_status",
            ),
            sa.ForeignKeyConstraint(["dataset_id"], ["behavior_datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["output_release_id"], ["agent_releases.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    for name, columns in (
        ("ix_dataset_training_runs_dataset_id", ["dataset_id"]),
        ("ix_dataset_training_runs_owner_id", ["owner_id"]),
        ("ix_dataset_training_runs_output_release_id", ["output_release_id"]),
    ):
        _ensure_index(name, "dataset_training_runs", columns)

    if not _has_table("population_packs"):
        op.create_table(
            "population_packs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("owner_id", sa.Integer()),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("visibility", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("spec", sa.JSON(), nullable=False),
            sa.Column("content_sha256", sa.String(length=64)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "status IN ('draft', 'published', 'verified')",
                name="ck_population_packs_status",
            ),
            sa.CheckConstraint(
                "visibility IN ('public', 'private')", name="ck_population_packs_visibility"
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "name", "version", name="uq_population_pack_version"),
        )
    _ensure_index("ix_population_packs_owner_id", "population_packs", ["owner_id"])
    _ensure_index("ix_population_packs_content_sha256", "population_packs", ["content_sha256"])

    # SQLite cannot add a named FK with ALTER TABLE, so use Alembic's batch
    # recreation path. The conditional checks also reconcile dev databases where
    # metadata.create_all created the new ecosystem tables before Alembic ran.
    vpp_columns = _column_names("vpps")
    vpp_foreign_keys = _foreign_key_names("vpps")
    vpp_indexes = _index_names("vpps")
    with op.batch_alter_table("vpps") as batch:
        if "release_id" not in vpp_columns:
            batch.add_column(sa.Column("release_id", sa.Integer()))
        if "release_content_sha256" not in vpp_columns:
            batch.add_column(sa.Column("release_content_sha256", sa.String(length=64)))
        if "fk_vpps_release_id" not in vpp_foreign_keys:
            batch.create_foreign_key(
                "fk_vpps_release_id",
                "agent_releases",
                ["release_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "ix_vpps_release_id" not in vpp_indexes:
            batch.create_index("ix_vpps_release_id", ["release_id"])

    snapshot_columns = _column_names("vpp_stat_snapshots")
    snapshot_additions = (
        sa.Column("release_id", sa.Integer()),
        sa.Column("release_content_sha256", sa.String(length=64)),
        sa.Column("imbalance_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("degradation_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("llm_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("gateway_rejections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fallback_count", sa.Integer(), nullable=False, server_default="0"),
    )
    for column in snapshot_additions:
        if column.name not in snapshot_columns:
            op.add_column("vpp_stat_snapshots", column)
    _ensure_index("ix_vpp_stat_snapshots_release_id", "vpp_stat_snapshots", ["release_id"])


def downgrade() -> None:
    op.drop_index("ix_vpp_stat_snapshots_release_id", table_name="vpp_stat_snapshots")
    for column in (
        "fallback_count",
        "gateway_rejections",
        "llm_cost_usd",
        "degradation_cost_usd",
        "imbalance_kwh",
        "release_content_sha256",
        "release_id",
    ):
        op.drop_column("vpp_stat_snapshots", column)

    with op.batch_alter_table("vpps") as batch:
        batch.drop_index("ix_vpps_release_id")
        batch.drop_constraint("fk_vpps_release_id", type_="foreignkey")
        batch.drop_column("release_content_sha256")
        batch.drop_column("release_id")

    op.drop_index("ix_population_packs_content_sha256", table_name="population_packs")
    op.drop_index("ix_population_packs_owner_id", table_name="population_packs")
    op.drop_table("population_packs")

    op.drop_index("ix_dataset_training_runs_output_release_id", table_name="dataset_training_runs")
    op.drop_index("ix_dataset_training_runs_owner_id", table_name="dataset_training_runs")
    op.drop_index("ix_dataset_training_runs_dataset_id", table_name="dataset_training_runs")
    op.drop_table("dataset_training_runs")

    for index in (
        "ix_behavior_datasets_market_status",
        "ix_behavior_datasets_content_sha256",
        "ix_behavior_datasets_source_release_id",
        "ix_behavior_datasets_parent_dataset_id",
        "ix_behavior_datasets_owner_id",
    ):
        op.drop_index(index, table_name="behavior_datasets")
    op.drop_table("behavior_datasets")

    op.drop_index("ix_release_eval_release_created", table_name="release_evaluations")
    op.drop_index("ix_release_evaluations_requested_by_id", table_name="release_evaluations")
    op.drop_index("ix_release_evaluations_release_id", table_name="release_evaluations")
    op.drop_table("release_evaluations")

    op.drop_index("ix_agent_releases_market_status", table_name="agent_releases")
    op.drop_index("ix_agent_releases_content_sha256", table_name="agent_releases")
    op.drop_index("ix_agent_releases_parent_release_id", table_name="agent_releases")
    op.drop_index("ix_agent_releases_owner_id", table_name="agent_releases")
    op.drop_table("agent_releases")
