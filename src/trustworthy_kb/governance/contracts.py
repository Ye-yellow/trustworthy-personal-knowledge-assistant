"""Strict provider-neutral contracts for claim and evidence governance."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator

from trustworthy_kb.domain import ClaimType, Sensitivity, SourceVersionId, TrustTier


class StrictContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClaimObject(StrictContract):
    value: Any
    value_type: str
    normalized_value: Any | None = None
    unit: str | None = None
    currency: str | None = None
    original_term: str | None = None


class ClaimScope(StrictContract):
    owner: str | None = None
    domain: str | None = None
    project: str | None = None
    geography: str | None = None
    version: str | None = None
    conditions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    polarity: Literal["positive", "negative"] = "positive"
    modality: str | None = None
    lifecycle_status: str | None = None


class ClaimOriginSpan(StrictContract):
    block_anchor: str = Field(min_length=1, max_length=1024)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def _end_after_start(self) -> ClaimOriginSpan:
        if self.end <= self.start:
            raise ValueError("origin span end must be after start")
        return self


class ClaimDraft(StrictContract):
    claim_type: ClaimType
    subject: str = Field(min_length=1, max_length=1024)
    predicate: str = Field(min_length=1, max_length=512)
    object: ClaimObject
    scope: ClaimScope = ClaimScope()
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    freshness_at: AwareDatetime | None = None
    sensitivity: Sensitivity
    origins: tuple[ClaimOriginSpan, ...] = Field(min_length=1)
    model_risk_hints: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _valid_time_range(self) -> ClaimDraft:
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be before valid_from")
        return self


class SearchIntent(StrEnum):
    SUPPORT = "SUPPORT"
    CHALLENGE = "CHALLENGE"


class SearchCapabilities(StrictContract):
    supports_responses_api: bool
    supports_native_web_search: bool
    supports_url_citations: bool
    returns_provider_request_id: bool
    supported_models: tuple[str, ...] = ()
    limits: dict[str, int | str | bool] = Field(default_factory=dict)


class PublicClaim(StrictContract):
    claim_type: ClaimType
    subject: str
    predicate: str
    object: ClaimObject
    scope: ClaimScope


class EvidenceSearchRequest(StrictContract):
    claim: PublicClaim
    intent: SearchIntent
    time_constraints: tuple[str, ...] = ()
    version_constraints: tuple[str, ...] = ()
    scope_constraints: tuple[str, ...] = ()
    max_results: int = Field(ge=1, le=50)
    policy_version: str = Field(min_length=1, max_length=100)
    idempotency_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvidenceSearchHit(StrictContract):
    candidate_id: str = Field(min_length=1, max_length=100)
    url: HttpUrl
    title: str = Field(min_length=1, max_length=2048)
    provider_request_id: str = Field(min_length=1, max_length=255)
    rank: int = Field(ge=0)
    citation_metadata: dict[str, str | int | bool] = Field(default_factory=dict)
    untrusted_snippet: str | None = None


class FetchedEvidenceBlock(StrictContract):
    anchor: str
    text: str
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FetchedEvidenceDocument(StrictContract):
    normalized_url: HttpUrl
    final_url: HttpUrl
    raw_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str
    byte_size: int = Field(ge=0)
    captured_at: AwareDatetime
    freshness_metadata_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete: bool
    extraction_status: str
    raw_snapshot_ref: str
    extracted_snapshot_ref: str
    blocks: tuple[FetchedEvidenceBlock, ...]
    safety_signals: tuple[str, ...] = ()


class EvidencePackCandidate(StrictContract):
    candidate_id: str
    source_version_id: SourceVersionId
    anchor: str
    excerpt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    trust_tier: TrustTier
    published_at: datetime | None = None
    version: str | None = None
    complete: bool
    evidence_family: str
    search_intent: SearchIntent


class EvidencePack(StrictContract):
    claim_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim: PublicClaim
    search_policy_version: str
    query_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    search_result_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ordered_candidate_ids: tuple[str, ...]
    candidates: tuple[EvidencePackCandidate, ...]
    max_candidates: int = Field(ge=1)
    max_evidence_blocks: int = Field(ge=1)
    truncation_reason: str | None = None


__all__ = [
    "ClaimDraft",
    "ClaimObject",
    "ClaimOriginSpan",
    "ClaimScope",
    "EvidencePack",
    "EvidencePackCandidate",
    "EvidenceSearchHit",
    "EvidenceSearchRequest",
    "FetchedEvidenceBlock",
    "FetchedEvidenceDocument",
    "PublicClaim",
    "SearchCapabilities",
    "SearchIntent",
]
