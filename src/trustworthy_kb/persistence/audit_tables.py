"""Internal SQLAlchemy mappings for model execution, audit, and idempotency."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Enum, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trustworthy_kb.domain.enums import (
    ActorType,
    EntityType,
    IdempotencyStatus,
    ModelRunPurpose,
    ModelRunStatus,
)
from trustworthy_kb.domain.ids import (
    IdempotencyRecordId,
    ModelRunId,
    OperationLogId,
    TypedId,
)
from trustworthy_kb.persistence.base import (
    Base,
    CreatedAtMixin,
    RevisionMixin,
    TimestampMixin,
    entity_id_check,
    id_prefix_check,
    sha256_check,
)
from trustworthy_kb.persistence.types import AnyTypedIdType, TypedIdType, UTCDateTime


class ModelRunTable(RevisionMixin, Base):
    __tablename__ = "model_runs"

    id: Mapped[ModelRunId] = mapped_column(TypedIdType(ModelRunId), primary_key=True)
    purpose: Mapped[ModelRunPurpose] = mapped_column(
        Enum(
            ModelRunPurpose,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="model_run_purpose",
        ),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ModelRunStatus] = mapped_column(
        Enum(
            ModelRunStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="model_run_status",
        ),
        nullable=False,
        default=ModelRunStatus.STARTED,
        server_default=ModelRunStatus.STARTED.value,
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    total_tokens: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", ModelRunId), name="model_run_id_prefix"),
        CheckConstraint("length(provider) > 0", name="model_run_provider_not_empty"),
        CheckConstraint("length(model) > 0", name="model_run_model_not_empty"),
        CheckConstraint("length(prompt_version) > 0", name="model_run_prompt_not_empty"),
        CheckConstraint(sha256_check("input_hash"), name="model_run_input_hash"),
        CheckConstraint(
            f"output_hash IS NULL OR ({sha256_check('output_hash')})",
            name="model_run_output_hash",
        ),
        CheckConstraint("input_tokens >= 0", name="model_run_input_tokens_nonnegative"),
        CheckConstraint("output_tokens >= 0", name="model_run_output_tokens_nonnegative"),
        CheckConstraint("total_tokens >= 0", name="model_run_total_tokens_nonnegative"),
        CheckConstraint(
            "total_tokens = input_tokens + output_tokens",
            name="model_run_token_total",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="model_run_latency_nonnegative",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="model_run_time_order",
        ),
        CheckConstraint("revision >= 1", name="model_run_revision_positive"),
    )


class OperationLogTable(CreatedAtMixin, Base):
    __tablename__ = "operation_logs"

    id: Mapped[OperationLogId] = mapped_column(TypedIdType(OperationLogId), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    step_number: Mapped[int] = mapped_column(nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(
            ActorType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="operation_log_actor_type",
        ),
        nullable=False,
    )
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[EntityType] = mapped_column(
        Enum(
            EntityType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="operation_log_target_type",
        ),
        nullable=False,
    )
    target_id: Mapped[TypedId] = mapped_column(AnyTypedIdType(), nullable=False, index=True)
    before_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    after_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    previous_entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", OperationLogId), name="operation_log_id_prefix"),
        CheckConstraint("length(operation_id) > 0", name="operation_log_operation_not_empty"),
        CheckConstraint("step_number >= 0", name="operation_log_step_nonnegative"),
        CheckConstraint("length(action) > 0", name="operation_log_action_not_empty"),
        CheckConstraint(
            entity_id_check("target_type", "target_id"),
            name="operation_log_target_id_type",
        ),
        CheckConstraint("json_valid(before_json)", name="operation_log_before_json_valid"),
        CheckConstraint("json_valid(after_json)", name="operation_log_after_json_valid"),
        CheckConstraint(
            f"previous_entry_hash IS NULL OR ({sha256_check('previous_entry_hash')})",
            name="operation_log_previous_hash",
        ),
        CheckConstraint(sha256_check("entry_hash"), name="operation_log_entry_hash"),
        UniqueConstraint("operation_id", "step_number", name="uq_operation_logs_step"),
        Index("ix_operation_logs_target", "target_type", "target_id"),
    )


class IdempotencyRecordTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "idempotency_records"

    id: Mapped[IdempotencyRecordId] = mapped_column(
        TypedIdType(IdempotencyRecordId),
        primary_key=True,
    )
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[IdempotencyStatus] = mapped_column(
        Enum(
            IdempotencyStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="idempotency_status",
        ),
        nullable=False,
        default=IdempotencyStatus.IN_PROGRESS,
        server_default=IdempotencyStatus.IN_PROGRESS.value,
    )
    result_type: Mapped[EntityType | None] = mapped_column(
        Enum(
            EntityType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="idempotency_result_type",
        ),
        nullable=True,
    )
    result_id: Mapped[TypedId | None] = mapped_column(AnyTypedIdType(), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempt: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(
            id_prefix_check("id", IdempotencyRecordId),
            name="idempotency_record_id_prefix",
        ),
        CheckConstraint("length(scope) > 0", name="idempotency_scope_not_empty"),
        CheckConstraint("length(idempotency_key) > 0", name="idempotency_key_not_empty"),
        CheckConstraint(sha256_check("request_hash"), name="idempotency_request_hash"),
        CheckConstraint(
            "(result_type IS NULL AND result_id IS NULL) OR "
            "(result_type IS NOT NULL AND result_id IS NOT NULL)",
            name="idempotency_result_pair",
        ),
        CheckConstraint(
            f"result_id IS NULL OR ({entity_id_check('result_type', 'result_id')})",
            name="idempotency_result_id_type",
        ),
        CheckConstraint("attempt >= 0", name="idempotency_attempt_nonnegative"),
        CheckConstraint("revision >= 1", name="idempotency_revision_positive"),
        UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_records_key"),
    )


__all__ = ["IdempotencyRecordTable", "ModelRunTable", "OperationLogTable"]
