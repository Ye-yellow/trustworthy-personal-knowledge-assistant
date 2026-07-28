"""Strict provider-neutral contracts for trusted question answering."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from trustworthy_kb.domain import (
    AnswerRunId,
    AnswerRunStatus,
    ClaimId,
    ClaimStatus,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeNoteId,
    Sensitivity,
    SourceVersionId,
)
from trustworthy_kb.domain.base import NonEmptyText, Sha256Hex

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class StrictAnswerContract(BaseModel):
    """Immutable answer value with an exact public shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class QueryScope(StrEnum):
    AUTO = "auto"
    GENERAL = "general"
    PERSONAL = "personal"


class PlannedScope(StrEnum):
    GENERAL = "general"
    PERSONAL = "personal"


AnswerStatus = AnswerRunStatus


class RefusalCode(StrEnum):
    NO_ACTIVE_GENERATION = "NO_ACTIVE_GENERATION"
    NO_TRUSTED_EVIDENCE = "NO_TRUSTED_EVIDENCE"
    EVIDENCE_NOT_LOCATABLE = "EVIDENCE_NOT_LOCATABLE"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    RETRIEVAL_UNAVAILABLE = "RETRIEVAL_UNAVAILABLE"
    PLANNING_FAILED = "PLANNING_FAILED"
    GENERATION_FAILED = "GENERATION_FAILED"
    CITATION_VALIDATION_FAILED = "CITATION_VALIDATION_FAILED"
    POLICY_BLOCKED = "POLICY_BLOCKED"


class AnswerEventType(StrEnum):
    ACCEPTED = "accepted"
    PLANNED = "planned"
    RETRIEVED = "retrieved"
    VERIFIED = "verified"
    ANSWER = "answer"
    REFUSAL = "refusal"


class AnswerRequest(StrictAnswerContract):
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    scope: QueryScope = QueryScope.AUTO
    as_of: AwareDatetime | None = None
    software_version: Annotated[str | None, Field(max_length=100)] = None
    top_k: Annotated[int, Field(ge=1, le=10)] = 5
    operation_id: Annotated[str | None, Field(max_length=200)] = None

    @field_validator("question")
    @classmethod
    def _safe_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or _CONTROL_CHARACTERS.search(normalized):
            raise ValueError("question contains unsupported control characters")
        return normalized

    @field_validator("software_version", "operation_id")
    @classmethod
    def _optional_single_line(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if "\n" in normalized or "\r" in normalized or _CONTROL_CHARACTERS.search(normalized):
            raise ValueError("optional request text must be a safe single line")
        return normalized


class QueryPlan(StrictAnswerContract):
    normalized_query: Annotated[str, Field(min_length=1, max_length=4000)]
    scope: PlannedScope
    requires_current: bool = False
    target_version: Annotated[str | None, Field(max_length=100)] = None
    include_opinions: bool = False

    @field_validator("normalized_query")
    @classmethod
    def _safe_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or _CONTROL_CHARACTERS.search(normalized):
            raise ValueError("normalized query contains unsupported control characters")
        return normalized

    @field_validator("target_version")
    @classmethod
    def _target_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AnswerEvidence(StrictAnswerContract):
    chunk_id: Sha256Hex
    text: Annotated[str, Field(min_length=1, max_length=20000)]
    claim_ids: tuple[ClaimId, ...] = Field(min_length=1)
    quality_status: ClaimStatus
    sensitivity: Sensitivity
    note_id: KnowledgeNoteId
    curated_version_id: CuratedVersionId
    generation_id: IndexGenerationId
    vault_path: Annotated[str, Field(min_length=1, max_length=500)]
    heading_path: tuple[Annotated[str, Field(max_length=200)], ...]
    source_version_ids: tuple[SourceVersionId, ...] = Field(min_length=1)

    @field_validator("claim_ids", "source_version_ids")
    @classmethod
    def _unique_lineage(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evidence lineage identifiers must be unique")
        return value

    @field_validator("vault_path")
    @classmethod
    def _safe_vault_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("Vault path must use forward slashes")
        path = PurePosixPath(value.strip())
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Vault path must be normalized and relative")
        return path.as_posix()

    @field_validator("heading_path")
    @classmethod
    def _safe_headings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(
            not item or "\n" in item or "\r" in item or _CONTROL_CHARACTERS.search(item)
            for item in normalized
        ):
            raise ValueError("evidence headings must be safe single-line text")
        return normalized


class DraftAnswerClaim(StrictAnswerContract):
    statement: Annotated[str, Field(min_length=1, max_length=1000)]
    citation_chunk_ids: tuple[Sha256Hex, ...] = Field(min_length=1, max_length=10)

    @field_validator("statement")
    @classmethod
    def _safe_statement(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or _CONTROL_CHARACTERS.search(normalized):
            raise ValueError("answer statement contains unsupported control characters")
        return normalized

    @field_validator("citation_chunk_ids")
    @classmethod
    def _unique_citations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("answer claim citations must be unique")
        return value


class AnswerDraft(StrictAnswerContract):
    claims: tuple[DraftAnswerClaim, ...] = Field(min_length=1, max_length=12)
    limitations: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = Field(
        default=(), max_length=5
    )

    @model_validator(mode="after")
    def _unique_statements(self) -> AnswerDraft:
        normalized = [claim.statement.casefold() for claim in self.claims]
        if len(set(normalized)) != len(normalized):
            raise ValueError("answer draft statements must be unique")
        return self


class CitationSupportDecision(StrictAnswerContract):
    claim_index: Annotated[int, Field(ge=0)]
    supported: bool
    supporting_chunk_ids: tuple[Sha256Hex, ...]
    reason_code: Annotated[str, Field(min_length=1, max_length=100)]

    @field_validator("supporting_chunk_ids")
    @classmethod
    def _unique_support(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("supporting citation IDs must be unique")
        return value


class CitationVerificationOutput(StrictAnswerContract):
    decisions: tuple[CitationSupportDecision, ...]


class AnswerCitation(StrictAnswerContract):
    number: Annotated[int, Field(ge=1)]
    chunk_id: Sha256Hex
    note_id: KnowledgeNoteId
    curated_version_id: CuratedVersionId
    source_version_ids: tuple[SourceVersionId, ...] = Field(min_length=1)
    quality_status: ClaimStatus
    vault_path: NonEmptyText
    heading_path: tuple[str, ...]
    wikilink: NonEmptyText


class AnsweredResult(StrictAnswerContract):
    status: Literal[AnswerStatus.ANSWERED] = AnswerStatus.ANSWERED
    run_id: AnswerRunId
    answer_markdown: NonEmptyText
    claims: tuple[DraftAnswerClaim, ...] = Field(min_length=1)
    citations: tuple[AnswerCitation, ...] = Field(min_length=1)
    generation_id: IndexGenerationId
    degraded: bool = False


class RefusedResult(StrictAnswerContract):
    status: Literal[AnswerStatus.REFUSED] = AnswerStatus.REFUSED
    run_id: AnswerRunId
    reason_code: RefusalCode
    message: NonEmptyText


type AnswerResult = AnsweredResult | RefusedResult


class AnswerEvent(StrictAnswerContract):
    event_id: Annotated[int, Field(ge=1)]
    event: AnswerEventType
    run_id: AnswerRunId
    occurred_at: AwareDatetime
    payload: dict[str, str | int | bool]


class GoldenCase(StrictAnswerContract):
    case_id: Annotated[str, Field(min_length=1, max_length=100)]
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    should_refuse: bool
    expected_chunk_ids: tuple[Sha256Hex, ...] = ()
    allowed_citation_chunk_ids: tuple[Sha256Hex, ...] = ()
    forbidden_citation_chunk_ids: tuple[Sha256Hex, ...] = ()
    expected_refusal_code: RefusalCode | None = None

    @model_validator(mode="after")
    def _consistent_expectation(self) -> GoldenCase:
        if self.should_refuse != (self.expected_refusal_code is not None):
            raise ValueError("refusal expectation and reason code must agree")
        if set(self.allowed_citation_chunk_ids).intersection(self.forbidden_citation_chunk_ids):
            raise ValueError("allowed and forbidden Golden citations must be disjoint")
        return self


class GoldenObservation(StrictAnswerContract):
    case_id: str
    refused: bool
    refusal_code: RefusalCode | None = None
    retrieved_chunk_ids: tuple[Sha256Hex, ...] = ()
    citation_chunk_ids: tuple[Sha256Hex, ...] = ()


class EvaluationMetrics(StrictAnswerContract):
    citation_precision: Annotated[float, Field(ge=0, le=1)]
    retrieval_recall: Annotated[float, Field(ge=0, le=1)]
    refusal_accuracy: Annotated[float, Field(ge=0, le=1)]
    unsafe_citation_count: Annotated[int, Field(ge=0)]
    case_count: Annotated[int, Field(ge=1)]


def utc_timestamp(value: datetime) -> datetime:
    """Type-narrowing helper used by API composition code."""

    if value.tzinfo is None:
        raise ValueError("answer timestamps must be timezone-aware")
    return value


__all__ = [
    "AnswerCitation",
    "AnswerDraft",
    "AnswerEvent",
    "AnswerEventType",
    "AnswerEvidence",
    "AnswerRequest",
    "AnswerResult",
    "AnswerStatus",
    "AnsweredResult",
    "CitationSupportDecision",
    "CitationVerificationOutput",
    "DraftAnswerClaim",
    "EvaluationMetrics",
    "GoldenCase",
    "GoldenObservation",
    "PlannedScope",
    "QueryPlan",
    "QueryScope",
    "RefusalCode",
    "RefusedResult",
]
