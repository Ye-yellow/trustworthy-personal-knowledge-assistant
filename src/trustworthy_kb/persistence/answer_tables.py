"""Internal SQLAlchemy mapping for hash-only trusted answer runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trustworthy_kb.domain import (
    AnswerRunId,
    AnswerRunStatus,
    AnswerScope,
    IndexGenerationId,
)
from trustworthy_kb.persistence.base import (
    Base,
    RevisionMixin,
    TimestampMixin,
    id_prefix_check,
    sha256_check,
    utc_now,
)
from trustworthy_kb.persistence.types import TypedIdType, UTCDateTime


class AnswerRunTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "answer_runs"

    id: Mapped[AnswerRunId] = mapped_column(TypedIdType(AnswerRunId), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[AnswerScope] = mapped_column(
        Enum(
            AnswerScope,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="answer_scope",
        ),
        nullable=False,
    )
    generation_id: Mapped[IndexGenerationId | None] = mapped_column(
        TypedIdType(IndexGenerationId),
        ForeignKey("index_generations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[AnswerRunStatus] = mapped_column(
        Enum(
            AnswerRunStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="answer_run_status",
        ),
        nullable=False,
        default=AnswerRunStatus.IN_PROGRESS,
        server_default=AnswerRunStatus.IN_PROGRESS.value,
    )
    refusal_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    answer_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    citation_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", AnswerRunId), name="answer_run_id_prefix"),
        CheckConstraint("length(operation_id) > 0", name="answer_run_operation_not_empty"),
        CheckConstraint(sha256_check("question_hash"), name="answer_run_question_hash"),
        CheckConstraint(
            "plan_hash IS NULL OR (" + sha256_check("plan_hash") + ")",
            name="answer_run_plan_hash",
        ),
        CheckConstraint(
            "answer_hash IS NULL OR (" + sha256_check("answer_hash") + ")",
            name="answer_run_answer_hash",
        ),
        CheckConstraint(
            "citation_manifest_hash IS NULL OR (" + sha256_check("citation_manifest_hash") + ")",
            name="answer_run_citation_manifest_hash",
        ),
        CheckConstraint("length(model_name) > 0", name="answer_run_model_not_empty"),
        CheckConstraint("length(prompt_version) > 0", name="answer_run_prompt_not_empty"),
        CheckConstraint("revision >= 1", name="answer_run_revision_positive"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="answer_run_completed_after_start",
        ),
        CheckConstraint(
            "(status = 'IN_PROGRESS' AND completed_at IS NULL AND refusal_code IS NULL "
            "AND answer_hash IS NULL AND citation_manifest_hash IS NULL) OR "
            "(status = 'ANSWERED' AND completed_at IS NOT NULL AND generation_id IS NOT NULL "
            "AND refusal_code IS NULL AND answer_hash IS NOT NULL "
            "AND citation_manifest_hash IS NOT NULL) OR "
            "(status = 'REFUSED' AND completed_at IS NOT NULL AND refusal_code IS NOT NULL "
            "AND answer_hash IS NULL AND citation_manifest_hash IS NULL) OR "
            "(status = 'FAILED' AND completed_at IS NOT NULL AND answer_hash IS NULL "
            "AND citation_manifest_hash IS NULL)",
            name="answer_run_terminal_shape",
        ),
        UniqueConstraint("operation_id", name="uq_answer_runs_operation"),
        Index("ix_answer_runs_question_hash", "question_hash"),
    )


__all__ = ["AnswerRunTable"]
