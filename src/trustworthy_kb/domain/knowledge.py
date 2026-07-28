"""Immutable domain records for claims, evidence, and quality decisions."""

from __future__ import annotations

from trustworthy_kb.domain.base import (
    AwareDatetime,
    DomainJson,
    DomainRecord,
    NonEmptyText,
    NonNegativeInt,
    Revision,
    Sha256Hex,
    UnitScore,
)
from trustworthy_kb.domain.enums import (
    ClaimStatus,
    ClaimType,
    EvidenceStance,
    QualityVerdict,
    Sensitivity,
)
from trustworthy_kb.domain.ids import (
    ClaimId,
    ContentBlockId,
    EvidenceFamilyId,
    EvidenceId,
    ModelRunId,
    QualityCheckId,
    SourceVersionId,
)


class ClaimRecord(DomainRecord):
    id: ClaimId
    claim_type: ClaimType
    subject: NonEmptyText
    predicate: NonEmptyText
    object_json: DomainJson
    scope_json: DomainJson
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    freshness_at: AwareDatetime | None = None
    sensitivity: Sensitivity
    status: ClaimStatus
    current_quality_check_id: QualityCheckId | None = None
    superseded_by_id: ClaimId | None = None
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    deleted_at: AwareDatetime | None = None


class ClaimOriginRecord(DomainRecord):
    claim_id: ClaimId
    content_block_id: ContentBlockId
    model_run_id: ModelRunId | None = None
    origin_span_json: DomainJson
    created_at: AwareDatetime


class EvidenceFamilyRecord(DomainRecord):
    id: EvidenceFamilyId
    canonical_origin: NonEmptyText
    origin_fingerprint: Sha256Hex
    created_at: AwareDatetime


class EvidenceRecord(DomainRecord):
    id: EvidenceId
    claim_id: ClaimId
    source_version_id: SourceVersionId
    evidence_family_id: EvidenceFamilyId
    anchor: NonEmptyText
    stance: EvidenceStance
    excerpt_hash: Sha256Hex
    relevance_score: UnitScore
    independence_score: UnitScore
    created_at: AwareDatetime


class QualityCheckRecord(DomainRecord):
    id: QualityCheckId
    claim_id: ClaimId
    policy_version: NonEmptyText
    verdict: QualityVerdict
    dimensions_json: DomainJson
    reason_code: NonEmptyText
    reason_summary: NonEmptyText
    evidence_snapshot_hash: Sha256Hex
    model_run_id: ModelRunId | None = None
    human_override: bool = False
    created_at: AwareDatetime


class QualityCheckEvidenceRecord(DomainRecord):
    quality_check_id: QualityCheckId
    evidence_id: EvidenceId
    position: NonNegativeInt


__all__ = [
    "ClaimOriginRecord",
    "ClaimRecord",
    "EvidenceFamilyRecord",
    "EvidenceRecord",
    "QualityCheckEvidenceRecord",
    "QualityCheckRecord",
]
