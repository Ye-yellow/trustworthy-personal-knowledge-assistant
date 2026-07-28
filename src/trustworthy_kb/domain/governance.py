"""Immutable records for claim-governance execution and human review."""

from __future__ import annotations

from trustworthy_kb.domain.base import (
    AwareDatetime,
    DomainRecord,
    NonEmptyText,
    NonNegativeInt,
    Revision,
    Sha256Hex,
)
from trustworthy_kb.domain.enums import (
    ActorType,
    GovernanceItemStage,
    GovernanceRunStatus,
    ReviewRequestStatus,
    RiskLevel,
)
from trustworthy_kb.domain.ids import (
    ClaimId,
    GovernanceItemId,
    GovernanceRunId,
    KnowledgeChangeId,
    QualityCheckId,
    ReviewRequestId,
    SourceVersionId,
)


class GovernanceRunRecord(DomainRecord):
    id: GovernanceRunId
    knowledge_change_id: KnowledgeChangeId
    target_source_version_id: SourceVersionId
    policy_version: NonEmptyText
    extractor_version: NonEmptyText
    verifier_version: NonEmptyText
    search_policy_version: NonEmptyText
    status: GovernanceRunStatus
    total_items: NonNegativeInt = 0
    decided_items: NonNegativeInt = 0
    review_items: NonNegativeInt = 0
    failed_items: NonNegativeInt = 0
    quarantined_items: NonNegativeInt = 0
    error_category: str | None = None
    revision: Revision
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class GovernanceItemRecord(DomainRecord):
    id: GovernanceItemId
    run_id: GovernanceRunId
    claim_id: ClaimId
    stage: GovernanceItemStage
    attempt: Revision
    risk_level: RiskLevel
    search_manifest_hash: Sha256Hex | None = None
    evidence_pack_hash: Sha256Hex | None = None
    current_quality_check_id: QualityCheckId | None = None
    error_category: str | None = None
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ReviewRequestRecord(DomainRecord):
    id: ReviewRequestId
    claim_id: ClaimId
    quality_check_id: QualityCheckId
    knowledge_change_id: KnowledgeChangeId
    risk_level: RiskLevel
    reason_code: NonEmptyText
    status: ReviewRequestStatus
    decision_reason_code: str | None = None
    decision_actor_type: ActorType | None = None
    decided_at: AwareDatetime | None = None
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime


__all__ = ["GovernanceItemRecord", "GovernanceRunRecord", "ReviewRequestRecord"]
