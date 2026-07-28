"""Immutable hash-only records for trusted answer execution."""

from __future__ import annotations

from trustworthy_kb.domain.base import (
    AwareDatetime,
    DomainRecord,
    NonEmptyText,
    Revision,
    Sha256Hex,
)
from trustworthy_kb.domain.enums import AnswerRunStatus, AnswerScope
from trustworthy_kb.domain.ids import AnswerRunId, IndexGenerationId


class AnswerRunRecord(DomainRecord):
    id: AnswerRunId
    operation_id: NonEmptyText
    question_hash: Sha256Hex
    plan_hash: Sha256Hex | None = None
    scope: AnswerScope
    generation_id: IndexGenerationId | None = None
    status: AnswerRunStatus
    refusal_code: str | None = None
    answer_hash: Sha256Hex | None = None
    citation_manifest_hash: Sha256Hex | None = None
    model_name: NonEmptyText
    prompt_version: NonEmptyText
    revision: Revision
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


__all__ = ["AnswerRunRecord"]
