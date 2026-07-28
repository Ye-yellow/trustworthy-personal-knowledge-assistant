"""Add L5 privacy-preserving trusted answer runs.

Revision ID: 20260728_0005
Revises: 20260728_0004
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0005"
down_revision: str | None = "20260728_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHA_CHECK = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"
_UTC_DEFAULT = sa.text("(strftime('%Y-%m-%dT%H:%M:%f000Z','now'))")


def upgrade() -> None:
    op.create_table(
        "answer_runs",
        sa.Column("id", sa.String(33), nullable=False),
        sa.Column("operation_id", sa.String(255), nullable=False),
        sa.Column("question_hash", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=True),
        sa.Column(
            "scope",
            sa.Enum(
                "general",
                "personal",
                name="answer_scope",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("generation_id", sa.String(33), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "IN_PROGRESS",
                "ANSWERED",
                "REFUSED",
                "FAILED",
                name="answer_run_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="IN_PROGRESS",
            nullable=False,
        ),
        sa.Column("refusal_code", sa.String(64), nullable=True),
        sa.Column("answer_hash", sa.String(64), nullable=True),
        sa.Column("citation_manifest_hash", sa.String(64), nullable=True),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_UTC_DEFAULT),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_UTC_DEFAULT),
        sa.CheckConstraint(
            "id LIKE 'ansrun_%' AND length(id) = 33",
            name=op.f("ck_answer_runs_answer_run_id_prefix"),
        ),
        sa.CheckConstraint(
            "length(operation_id) > 0",
            name=op.f("ck_answer_runs_answer_run_operation_not_empty"),
        ),
        sa.CheckConstraint(
            _SHA_CHECK.format("question_hash"),
            name=op.f("ck_answer_runs_answer_run_question_hash"),
        ),
        sa.CheckConstraint(
            "plan_hash IS NULL OR (" + _SHA_CHECK.format("plan_hash") + ")",
            name=op.f("ck_answer_runs_answer_run_plan_hash"),
        ),
        sa.CheckConstraint(
            "answer_hash IS NULL OR (" + _SHA_CHECK.format("answer_hash") + ")",
            name=op.f("ck_answer_runs_answer_run_answer_hash"),
        ),
        sa.CheckConstraint(
            "citation_manifest_hash IS NULL OR ("
            + _SHA_CHECK.format("citation_manifest_hash")
            + ")",
            name=op.f("ck_answer_runs_answer_run_citation_manifest_hash"),
        ),
        sa.CheckConstraint(
            "length(model_name) > 0",
            name=op.f("ck_answer_runs_answer_run_model_not_empty"),
        ),
        sa.CheckConstraint(
            "length(prompt_version) > 0",
            name=op.f("ck_answer_runs_answer_run_prompt_not_empty"),
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name=op.f("ck_answer_runs_answer_run_revision_positive"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name=op.f("ck_answer_runs_answer_run_completed_after_start"),
        ),
        sa.CheckConstraint(
            "(status = 'IN_PROGRESS' AND completed_at IS NULL AND refusal_code IS NULL "
            "AND answer_hash IS NULL AND citation_manifest_hash IS NULL) OR "
            "(status = 'ANSWERED' AND completed_at IS NOT NULL AND generation_id IS NOT NULL "
            "AND refusal_code IS NULL AND answer_hash IS NOT NULL "
            "AND citation_manifest_hash IS NOT NULL) OR "
            "(status = 'REFUSED' AND completed_at IS NOT NULL AND refusal_code IS NOT NULL "
            "AND answer_hash IS NULL AND citation_manifest_hash IS NULL) OR "
            "(status = 'FAILED' AND completed_at IS NOT NULL AND answer_hash IS NULL "
            "AND citation_manifest_hash IS NULL)",
            name=op.f("ck_answer_runs_answer_run_terminal_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["index_generations.id"],
            name=op.f("fk_answer_runs_generation_id_index_generations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_runs")),
        sa.UniqueConstraint("operation_id", name="uq_answer_runs_operation"),
    )
    op.create_index("ix_answer_runs_question_hash", "answer_runs", ["question_hash"])


def downgrade() -> None:
    op.drop_table("answer_runs")
