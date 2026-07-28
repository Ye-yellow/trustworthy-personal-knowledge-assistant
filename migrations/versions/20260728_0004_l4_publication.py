"""Add L4 publication Saga and index-generation metadata.

Revision ID: 20260728_0004
Revises: 20260728_0003
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0004"
down_revision: str | None = "20260728_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHA_CHECK = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"
_UTC_DEFAULT = sa.text("(strftime('%Y-%m-%dT%H:%M:%f000Z','now'))")


def _extend_curated_versions() -> None:
    op.add_column(
        "curated_versions",
        sa.Column(
            "staging_path",
            sa.String(2048),
            sa.CheckConstraint(
                "staging_path IS NULL OR length(staging_path) > 0",
                name=op.f("ck_curated_versions_curated_version_staging_path_not_empty"),
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "curated_versions",
        sa.Column(
            "claim_set_hash",
            sa.String(64),
            sa.CheckConstraint(
                "claim_set_hash IS NULL OR (" + _SHA_CHECK.format("claim_set_hash") + ")",
                name=op.f("ck_curated_versions_curated_version_claim_set_hash"),
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "curated_versions",
        sa.Column(
            "operation_id",
            sa.String(255),
            sa.CheckConstraint(
                "operation_id IS NULL OR length(operation_id) > 0",
                name=op.f("ck_curated_versions_curated_version_operation_not_empty"),
            ),
            nullable=True,
        ),
    )
    op.add_column("curated_versions", sa.Column("published_at", sa.String(27), nullable=True))
    op.create_index("ix_curated_versions_operation_id", "curated_versions", ["operation_id"])


def _extend_index_generations() -> None:
    op.add_column(
        "index_generations",
        sa.Column(
            "collection_name",
            sa.String(255),
            sa.CheckConstraint(
                "length(collection_name) > 0",
                name=op.f("ck_index_generations_index_generation_collection_name_not_empty"),
            ),
            server_default="unconfigured",
            nullable=False,
        ),
    )
    op.add_column(
        "index_generations",
        sa.Column(
            "embedding_dimension",
            sa.Integer(),
            sa.CheckConstraint(
                "embedding_dimension >= 1",
                name=op.f("ck_index_generations_index_generation_embedding_dimension_positive"),
            ),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "index_generations",
        sa.Column(
            "schema_version",
            sa.String(100),
            sa.CheckConstraint(
                "length(schema_version) > 0",
                name=op.f("ck_index_generations_index_generation_schema_version_not_empty"),
            ),
            server_default="legacy-v1",
            nullable=False,
        ),
    )
    op.add_column(
        "index_generations",
        sa.Column(
            "manifest_hash",
            sa.String(64),
            sa.CheckConstraint(
                _SHA_CHECK.format("manifest_hash"),
                name=op.f("ck_index_generations_index_generation_manifest_hash"),
            ),
            server_default="0" * 64,
            nullable=False,
        ),
    )


def _extend_index_jobs() -> None:
    op.add_column(
        "index_jobs",
        sa.Column(
            "content_hash",
            sa.String(64),
            sa.CheckConstraint(
                "content_hash IS NULL OR (" + _SHA_CHECK.format("content_hash") + ")",
                name=op.f("ck_index_jobs_index_job_content_hash"),
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "index_jobs",
        sa.Column(
            "indexed_chunk_count",
            sa.Integer(),
            sa.CheckConstraint(
                "indexed_chunk_count >= 0",
                name=op.f("ck_index_jobs_index_job_chunk_count_nonnegative"),
            ),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "index_jobs",
        sa.Column(
            "operation_id",
            sa.String(255),
            sa.CheckConstraint(
                "operation_id IS NULL OR length(operation_id) > 0",
                name=op.f("ck_index_jobs_index_job_operation_not_empty"),
            ),
            nullable=True,
        ),
    )
    op.add_column("index_jobs", sa.Column("last_verified_at", sa.String(27), nullable=True))
    op.create_index("ix_index_jobs_operation_id", "index_jobs", ["operation_id"])


def _create_publication_runs() -> None:
    op.create_table(
        "publication_runs",
        sa.Column("id", sa.String(33), nullable=False),
        sa.Column("knowledge_change_id", sa.String(33), nullable=False),
        sa.Column("note_id", sa.String(31), nullable=False),
        sa.Column("curated_version_id", sa.String(34), nullable=False),
        sa.Column("target_generation_id", sa.String(33), nullable=False),
        sa.Column("operation_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(15), server_default="PLANNING", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("error_category", sa.String(100), nullable=True),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("completed_at", sa.String(27), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_at", sa.String(27), server_default=_UTC_DEFAULT, nullable=False),
        sa.Column("created_at", sa.String(27), server_default=_UTC_DEFAULT, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publication_runs")),
        sa.ForeignKeyConstraint(
            ["knowledge_change_id"],
            ["knowledge_changes.id"],
            name=op.f("fk_publication_runs_knowledge_change_id_knowledge_changes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["knowledge_notes.id"],
            name=op.f("fk_publication_runs_note_id_knowledge_notes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["curated_version_id"],
            ["curated_versions.id"],
            name=op.f("fk_publication_runs_curated_version_id_curated_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_generation_id"],
            ["index_generations.id"],
            name=op.f("fk_publication_runs_target_generation_id_index_generations"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("operation_id", name=op.f("uq_publication_runs_operation_id")),
        sa.UniqueConstraint(
            "knowledge_change_id",
            "target_generation_id",
            name="uq_publication_runs_change_generation",
        ),
        sa.CheckConstraint(
            "id LIKE 'pubrun_%' AND length(id) = 33",
            name=op.f("ck_publication_runs_publication_run_id_prefix"),
        ),
        sa.CheckConstraint(
            "length(operation_id) > 0",
            name=op.f("ck_publication_runs_publication_run_operation_not_empty"),
        ),
        sa.CheckConstraint(
            "status IN ('PLANNING','CURATING','VAULT_STAGED','INDEXING','INDEX_VERIFIED',"
            "'VAULT_PUBLISHED','ACTIVATING','COMPLETED','FAILED')",
            name=op.f("ck_publication_runs_publication_run_status"),
        ),
        sa.CheckConstraint(
            "attempt >= 1",
            name=op.f("ck_publication_runs_publication_run_attempt_positive"),
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name=op.f("ck_publication_runs_publication_run_revision_positive"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name=op.f("ck_publication_runs_publication_run_completed_after_start"),
        ),
    )
    op.create_index(
        "ix_publication_runs_knowledge_change_id",
        "publication_runs",
        ["knowledge_change_id"],
    )


def upgrade() -> None:
    _extend_curated_versions()
    _extend_index_generations()
    _extend_index_jobs()
    _create_publication_runs()


def downgrade() -> None:
    op.drop_table("publication_runs")
    op.drop_index("ix_index_jobs_operation_id", table_name="index_jobs")
    op.drop_column("index_jobs", "last_verified_at")
    op.drop_column("index_jobs", "operation_id")
    op.drop_column("index_jobs", "indexed_chunk_count")
    op.drop_column("index_jobs", "content_hash")
    op.drop_column("index_generations", "manifest_hash")
    op.drop_column("index_generations", "schema_version")
    op.drop_column("index_generations", "embedding_dimension")
    op.drop_column("index_generations", "collection_name")
    op.drop_index("ix_curated_versions_operation_id", table_name="curated_versions")
    op.drop_column("curated_versions", "published_at")
    op.drop_column("curated_versions", "operation_id")
    op.drop_column("curated_versions", "claim_set_hash")
    op.drop_column("curated_versions", "staging_path")
