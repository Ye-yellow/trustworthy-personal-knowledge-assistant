"""Strict provider-neutral contracts for the L4 publication pipeline."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from trustworthy_kb.domain import (
    ClaimId,
    ClaimStatus,
    ClaimType,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeChangeId,
    KnowledgeNoteId,
    Sensitivity,
    SourceId,
    SourceVersionId,
)
from trustworthy_kb.domain.base import DomainJson, NonEmptyText, Sha256Hex


class StrictContract(BaseModel):
    """Immutable strict-by-shape publication value."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class CurationClaim(StrictContract):
    id: ClaimId
    claim_type: ClaimType
    subject: NonEmptyText
    predicate: NonEmptyText
    object_json: DomainJson
    status: ClaimStatus
    sensitivity: Sensitivity
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    freshness_at: AwareDatetime | None = None


class CurationGroup(StrictContract):
    heading: NonEmptyText
    claim_ids: tuple[ClaimId, ...] = Field(min_length=1)

    @field_validator("claim_ids")
    @classmethod
    def _unique_claim_ids(cls, value: tuple[ClaimId, ...]) -> tuple[ClaimId, ...]:
        if len(set(value)) != len(value):
            raise ValueError("curation group claim IDs must be unique")
        return value


class CurationPlan(StrictContract):
    title: NonEmptyText
    groups: tuple[CurationGroup, ...] = Field(min_length=1)


class CurationArtifact(StrictContract):
    note_id: KnowledgeNoteId
    curated_version_id: CuratedVersionId
    based_on_change_id: KnowledgeChangeId
    version_number: Annotated[int, Field(ge=1)]
    title: NonEmptyText
    body_markdown: Annotated[str, Field(min_length=1)]
    markdown: Annotated[str, Field(min_length=1)]
    claim_ids: tuple[ClaimId, ...] = Field(min_length=1)
    quality_statuses: tuple[ClaimStatus, ...] = Field(min_length=1)
    source_ids: tuple[SourceId, ...] = Field(min_length=1)
    source_version_ids: tuple[SourceVersionId, ...] = Field(min_length=1)
    sensitivity: Sensitivity
    model_name: NonEmptyText
    prompt_version: NonEmptyText
    quality_policy_version: NonEmptyText
    content_hash: Sha256Hex
    created_at: AwareDatetime


class KnowledgeChunk(StrictContract):
    chunk_id: Sha256Hex
    note_id: KnowledgeNoteId
    curated_version_id: CuratedVersionId
    claim_ids: tuple[ClaimId, ...] = Field(min_length=1)
    text: NonEmptyText
    heading_path: tuple[str, ...]
    ordinal: Annotated[int, Field(ge=0)]
    quality_status: ClaimStatus
    sensitivity: Sensitivity
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    freshness_at: AwareDatetime | None = None
    generation_id: IndexGenerationId
    generation_number: Annotated[int, Field(ge=1)]
    embedding_model: NonEmptyText
    chunker_version: NonEmptyText
    content_hash: Sha256Hex


class IndexedChunk(StrictContract):
    chunk: KnowledgeChunk
    dense: tuple[float, ...] = Field(min_length=1)

    @field_validator("dense")
    @classmethod
    def _finite_vector(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not math.isfinite(item) for item in value):
            raise ValueError("embedding vector values must be finite")
        return value


class IndexProbe(StrictContract):
    chunk_id: Sha256Hex
    curated_version_id: CuratedVersionId
    content_hash: Sha256Hex


class RetrievalMode(StrEnum):
    HYBRID = "hybrid"
    BM25_ONLY = "bm25_only"


class RetrievalQuery(StrictContract):
    text: NonEmptyText
    top_k: Annotated[int, Field(ge=1, le=100)] = 5
    candidate_k: Annotated[int, Field(ge=1, le=500)] = 30
    allowed_quality_statuses: tuple[ClaimStatus, ...] = (ClaimStatus.VERIFIED,)
    max_sensitivity: Sensitivity = Sensitivity.PRIVATE
    at: AwareDatetime
    allow_stale: bool = False

    @field_validator("allowed_quality_statuses")
    @classmethod
    def _quality_filter_is_safe(cls, value: tuple[ClaimStatus, ...]) -> tuple[ClaimStatus, ...]:
        forbidden = {
            ClaimStatus.INSUFFICIENT,
            ClaimStatus.OUTDATED,
            ClaimStatus.REJECTED,
            ClaimStatus.SUPERSEDED,
            ClaimStatus.QUARANTINED,
        }
        if not value or forbidden.intersection(value):
            raise ValueError("retrieval quality filter contains a forbidden status")
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def _candidate_count(self) -> RetrievalQuery:
        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be at least top_k")
        return self


class VectorSearchRequest(StrictContract):
    query: RetrievalQuery
    dense: tuple[float, ...]
    generation_number: Annotated[int, Field(ge=1)]
    rrf_k: Annotated[int, Field(ge=1, le=10000)] = 60


class VectorSearchHit(StrictContract):
    chunk: KnowledgeChunk
    score: float

    @field_validator("score")
    @classmethod
    def _finite_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("retrieval score must be finite")
        return value


class RerankItem(StrictContract):
    chunk_id: Sha256Hex
    text: NonEmptyText
    score: float


class RetrievalHit(StrictContract):
    chunk: KnowledgeChunk
    retrieval_score: float
    rerank_score: float | None = None


class RetrievalResult(StrictContract):
    hits: tuple[RetrievalHit, ...]
    mode: RetrievalMode
    degraded: bool
    generation_id: IndexGenerationId


class ReconciliationSeverity(StrEnum):
    HEALTHY = "healthy"
    REPAIRABLE = "repairable"
    BLOCKED = "blocked"


class ReconciliationFinding(StrictContract):
    code: NonEmptyText
    severity: ReconciliationSeverity
    note_id: KnowledgeNoteId | None = None
    curated_version_id: CuratedVersionId | None = None
    relative_path: str | None = None
    repaired: bool = False


class ReconciliationReport(StrictContract):
    findings: tuple[ReconciliationFinding, ...]
    checked_at: AwareDatetime

    @property
    def blocked(self) -> bool:
        return any(item.severity is ReconciliationSeverity.BLOCKED for item in self.findings)


def utc_milliseconds(value: datetime | None) -> int:
    """Return a stable Milvus scalar representation; zero means unbounded."""

    return 0 if value is None else int(value.timestamp() * 1000)


__all__ = [
    "CurationArtifact",
    "CurationClaim",
    "CurationGroup",
    "CurationPlan",
    "IndexProbe",
    "IndexedChunk",
    "KnowledgeChunk",
    "ReconciliationFinding",
    "ReconciliationReport",
    "ReconciliationSeverity",
    "RerankItem",
    "RetrievalHit",
    "RetrievalMode",
    "RetrievalQuery",
    "RetrievalResult",
    "StrictContract",
    "VectorSearchHit",
    "VectorSearchRequest",
    "utc_milliseconds",
]
