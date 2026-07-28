"""Immutable domain records for model execution, audit, and idempotency."""

from __future__ import annotations

from trustworthy_kb.domain.base import (
    AwareDatetime,
    DomainJson,
    DomainRecord,
    NonEmptyText,
    NonNegativeInt,
    Revision,
    Sha256Hex,
)
from trustworthy_kb.domain.enums import (
    ActorType,
    EntityType,
    IdempotencyStatus,
    ModelRunPurpose,
    ModelRunStatus,
)
from trustworthy_kb.domain.ids import IdempotencyRecordId, ModelRunId, OperationLogId, TypedId


class ModelRunRecord(DomainRecord):
    id: ModelRunId
    purpose: ModelRunPurpose
    provider: NonEmptyText
    model: NonEmptyText
    prompt_version: NonEmptyText
    status: ModelRunStatus
    input_hash: Sha256Hex
    output_hash: Sha256Hex | None = None
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0
    latency_ms: NonNegativeInt | None = None
    request_id: str | None = None
    error_category: str | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    revision: Revision


class OperationLogRecord(DomainRecord):
    id: OperationLogId
    operation_id: NonEmptyText
    step_number: NonNegativeInt
    actor_type: ActorType
    actor_id: str | None = None
    action: NonEmptyText
    target_type: EntityType
    target_id: TypedId
    before_json: DomainJson
    after_json: DomainJson
    previous_entry_hash: Sha256Hex | None = None
    entry_hash: Sha256Hex
    created_at: AwareDatetime


class IdempotencyRecord(DomainRecord):
    id: IdempotencyRecordId
    scope: NonEmptyText
    idempotency_key: NonEmptyText
    request_hash: Sha256Hex
    status: IdempotencyStatus
    result_type: EntityType | None = None
    result_id: TypedId | None = None
    lease_owner: str | None = None
    lease_expires_at: AwareDatetime | None = None
    attempt: NonNegativeInt
    error_category: str | None = None
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    expires_at: AwareDatetime | None = None


__all__ = ["IdempotencyRecord", "ModelRunRecord", "OperationLogRecord"]
