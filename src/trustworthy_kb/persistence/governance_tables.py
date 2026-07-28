"""Internal SQLAlchemy mappings for governance runs, items, and reviews."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from trustworthy_kb.domain import (
    ActorType,
    ClaimId,
    GovernanceItemId,
    GovernanceItemStage,
    GovernanceRunId,
    GovernanceRunStatus,
    KnowledgeChangeId,
    QualityCheckId,
    ReviewRequestId,
    ReviewRequestStatus,
    RiskLevel,
    SourceVersionId,
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


def _enum(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda enum: [item.value for item in enum],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        name=name,
    )


class GovernanceRunTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "governance_runs"

    id: Mapped[GovernanceRunId] = mapped_column(TypedIdType(GovernanceRunId), primary_key=True)
    knowledge_change_id: Mapped[KnowledgeChangeId] = mapped_column(
        TypedIdType(KnowledgeChangeId),
        ForeignKey("knowledge_changes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_source_version_id: Mapped[SourceVersionId] = mapped_column(
        TypedIdType(SourceVersionId),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    verifier_version: Mapped[str] = mapped_column(String(100), nullable=False)
    search_policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[GovernanceRunStatus] = mapped_column(
        _enum(GovernanceRunStatus, "governance_run_status"),
        nullable=False,
        default=GovernanceRunStatus.PLANNING,
        server_default=GovernanceRunStatus.PLANNING.value,
    )
    total_items: Mapped[int] = mapped_column(default=0, server_default=text("0"), nullable=False)
    decided_items: Mapped[int] = mapped_column(default=0, server_default=text("0"), nullable=False)
    review_items: Mapped[int] = mapped_column(default=0, server_default=text("0"), nullable=False)
    failed_items: Mapped[int] = mapped_column(default=0, server_default=text("0"), nullable=False)
    quarantined_items: Mapped[int] = mapped_column(
        default=0, server_default=text("0"), nullable=False
    )
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%f000Z','now'))"),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", GovernanceRunId), name="governance_run_id_prefix"),
        CheckConstraint("length(policy_version) > 0", name="governance_policy_not_empty"),
        CheckConstraint("length(extractor_version) > 0", name="governance_extractor_not_empty"),
        CheckConstraint("length(verifier_version) > 0", name="governance_verifier_not_empty"),
        CheckConstraint(
            "length(search_policy_version) > 0", name="governance_search_policy_not_empty"
        ),
        CheckConstraint(
            "total_items >= 0 AND decided_items >= 0 AND review_items >= 0 "
            "AND failed_items >= 0 AND quarantined_items >= 0",
            name="governance_run_counts_nonnegative",
        ),
        CheckConstraint(
            "decided_items + review_items + failed_items + quarantined_items <= total_items",
            name="governance_run_counts_bounded",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="governance_run_completed_after_start",
        ),
        CheckConstraint("revision >= 1", name="governance_run_revision_positive"),
        UniqueConstraint(
            "knowledge_change_id", "policy_version", name="uq_governance_runs_change_policy"
        ),
        Index("ix_governance_runs_change_id", "knowledge_change_id"),
    )


class GovernanceItemTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "governance_items"

    id: Mapped[GovernanceItemId] = mapped_column(TypedIdType(GovernanceItemId), primary_key=True)
    run_id: Mapped[GovernanceRunId] = mapped_column(
        TypedIdType(GovernanceRunId),
        ForeignKey("governance_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    claim_id: Mapped[ClaimId] = mapped_column(
        TypedIdType(ClaimId), ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False
    )
    stage: Mapped[GovernanceItemStage] = mapped_column(
        _enum(GovernanceItemStage, "governance_item_stage"),
        nullable=False,
        default=GovernanceItemStage.EXTRACTED,
        server_default=GovernanceItemStage.EXTRACTED.value,
    )
    attempt: Mapped[int] = mapped_column(default=1, server_default=text("1"), nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(
        _enum(RiskLevel, "governance_risk_level"), nullable=False
    )
    search_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_pack_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_quality_check_id: Mapped[QualityCheckId | None] = mapped_column(
        TypedIdType(QualityCheckId),
        ForeignKey("quality_checks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", GovernanceItemId), name="governance_item_id_prefix"),
        CheckConstraint("attempt >= 1", name="governance_item_attempt_positive"),
        CheckConstraint(
            "search_manifest_hash IS NULL OR (" + sha256_check("search_manifest_hash") + ")",
            name="governance_item_search_manifest_hash",
        ),
        CheckConstraint(
            "evidence_pack_hash IS NULL OR (" + sha256_check("evidence_pack_hash") + ")",
            name="governance_item_evidence_pack_hash",
        ),
        CheckConstraint("revision >= 1", name="governance_item_revision_positive"),
        UniqueConstraint("run_id", "claim_id", name="uq_governance_items_run_claim"),
        Index("ix_governance_items_run_stage", "run_id", "stage"),
    )


class ReviewRequestTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "review_requests"

    id: Mapped[ReviewRequestId] = mapped_column(TypedIdType(ReviewRequestId), primary_key=True)
    claim_id: Mapped[ClaimId] = mapped_column(
        TypedIdType(ClaimId), ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False
    )
    quality_check_id: Mapped[QualityCheckId] = mapped_column(
        TypedIdType(QualityCheckId),
        ForeignKey("quality_checks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    knowledge_change_id: Mapped[KnowledgeChangeId] = mapped_column(
        TypedIdType(KnowledgeChangeId),
        ForeignKey("knowledge_changes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        _enum(RiskLevel, "review_risk_level"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ReviewRequestStatus] = mapped_column(
        _enum(ReviewRequestStatus, "review_request_status"),
        nullable=False,
        default=ReviewRequestStatus.PENDING,
        server_default=ReviewRequestStatus.PENDING.value,
    )
    decision_reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision_actor_type: Mapped[ActorType | None] = mapped_column(
        _enum(ActorType, "review_decision_actor_type"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", ReviewRequestId), name="review_request_id_prefix"),
        CheckConstraint("length(reason_code) > 0", name="review_reason_not_empty"),
        CheckConstraint(
            "(status = 'PENDING' AND decision_reason_code IS NULL AND "
            "decision_actor_type IS NULL AND decided_at IS NULL) OR "
            "(status <> 'PENDING' AND decision_reason_code IS NOT NULL AND "
            "decision_actor_type IS NOT NULL AND decided_at IS NOT NULL)",
            name="review_decision_consistent",
        ),
        CheckConstraint("revision >= 1", name="review_request_revision_positive"),
        Index(
            "uq_review_requests_live_quality_check",
            "quality_check_id",
            unique=True,
            sqlite_where=status == ReviewRequestStatus.PENDING,
        ),
        Index("ix_review_requests_status", "status"),
    )


__all__ = ["GovernanceItemTable", "GovernanceRunTable", "ReviewRequestTable"]
