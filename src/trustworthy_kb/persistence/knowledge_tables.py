"""Internal SQLAlchemy mappings for claims, evidence, and quality checks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

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
from trustworthy_kb.persistence.base import (
    Base,
    CreatedAtMixin,
    RevisionMixin,
    TimestampMixin,
    id_prefix_check,
    sha256_check,
)
from trustworthy_kb.persistence.types import TypedIdType, UTCDateTime


class ClaimTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "claims"

    id: Mapped[ClaimId] = mapped_column(TypedIdType(ClaimId), primary_key=True)
    claim_type: Mapped[ClaimType] = mapped_column(
        Enum(
            ClaimType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="claim_type",
        ),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(1024), nullable=False)
    predicate: Mapped[str] = mapped_column(String(512), nullable=False)
    object_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    valid_from: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    freshness_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    sensitivity: Mapped[Sensitivity] = mapped_column(
        Enum(
            Sensitivity,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="claim_sensitivity",
        ),
        nullable=False,
    )
    status: Mapped[ClaimStatus] = mapped_column(
        Enum(
            ClaimStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="claim_status",
        ),
        nullable=False,
        default=ClaimStatus.PROPOSED,
        server_default=ClaimStatus.PROPOSED.value,
    )
    current_quality_check_id: Mapped[QualityCheckId | None] = mapped_column(
        TypedIdType(QualityCheckId),
        ForeignKey("quality_checks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    superseded_by_id: Mapped[ClaimId | None] = mapped_column(
        TypedIdType(ClaimId),
        ForeignKey("claims.id", ondelete="RESTRICT"),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", ClaimId), name="claim_id_prefix"),
        CheckConstraint("length(subject) > 0", name="claim_subject_not_empty"),
        CheckConstraint("length(predicate) > 0", name="claim_predicate_not_empty"),
        CheckConstraint("json_valid(object_json)", name="claim_object_json_valid"),
        CheckConstraint("json_valid(scope_json)", name="claim_scope_json_valid"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="claim_valid_range",
        ),
        CheckConstraint(
            "superseded_by_id IS NULL OR superseded_by_id <> id",
            name="claim_not_self_superseded",
        ),
        CheckConstraint("revision >= 1", name="claim_revision_positive"),
    )


class ClaimOriginTable(CreatedAtMixin, Base):
    __tablename__ = "claim_origins"

    claim_id: Mapped[ClaimId] = mapped_column(
        TypedIdType(ClaimId),
        ForeignKey("claims.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    content_block_id: Mapped[ContentBlockId] = mapped_column(
        TypedIdType(ContentBlockId),
        ForeignKey("content_blocks.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    model_run_id: Mapped[ModelRunId | None] = mapped_column(
        TypedIdType(ModelRunId),
        ForeignKey("model_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    origin_span_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        CheckConstraint("json_valid(origin_span_json)", name="claim_origin_span_json_valid"),
    )


class EvidenceFamilyTable(CreatedAtMixin, Base):
    __tablename__ = "evidence_families"

    id: Mapped[EvidenceFamilyId] = mapped_column(TypedIdType(EvidenceFamilyId), primary_key=True)
    canonical_origin: Mapped[str] = mapped_column(String(2048), nullable=False)
    origin_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    __table_args__ = (
        CheckConstraint(
            id_prefix_check("id", EvidenceFamilyId),
            name="evidence_family_id_prefix",
        ),
        CheckConstraint(
            "length(canonical_origin) > 0",
            name="evidence_family_origin_not_empty",
        ),
        CheckConstraint(
            sha256_check("origin_fingerprint"),
            name="evidence_family_fingerprint",
        ),
    )


class EvidenceTable(CreatedAtMixin, Base):
    __tablename__ = "evidence"

    id: Mapped[EvidenceId] = mapped_column(TypedIdType(EvidenceId), primary_key=True)
    claim_id: Mapped[ClaimId] = mapped_column(
        TypedIdType(ClaimId),
        ForeignKey("claims.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_version_id: Mapped[SourceVersionId] = mapped_column(
        TypedIdType(SourceVersionId),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_family_id: Mapped[EvidenceFamilyId] = mapped_column(
        TypedIdType(EvidenceFamilyId),
        ForeignKey("evidence_families.id", ondelete="RESTRICT"),
        nullable=False,
    )
    anchor: Mapped[str] = mapped_column(String(1024), nullable=False)
    stance: Mapped[EvidenceStance] = mapped_column(
        Enum(
            EvidenceStance,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="evidence_stance",
        ),
        nullable=False,
    )
    excerpt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    independence_score: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", EvidenceId), name="evidence_id_prefix"),
        CheckConstraint("length(anchor) > 0", name="evidence_anchor_not_empty"),
        CheckConstraint(sha256_check("excerpt_hash"), name="evidence_excerpt_hash"),
        CheckConstraint(
            "relevance_score >= 0 AND relevance_score <= 1",
            name="evidence_relevance_range",
        ),
        CheckConstraint(
            "independence_score >= 0 AND independence_score <= 1",
            name="evidence_independence_range",
        ),
        UniqueConstraint(
            "claim_id",
            "source_version_id",
            "anchor",
            "stance",
            name="uq_evidence_claim_location_stance",
        ),
    )


class QualityCheckTable(CreatedAtMixin, Base):
    __tablename__ = "quality_checks"

    id: Mapped[QualityCheckId] = mapped_column(TypedIdType(QualityCheckId), primary_key=True)
    claim_id: Mapped[ClaimId] = mapped_column(
        TypedIdType(ClaimId),
        ForeignKey("claims.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    verdict: Mapped[QualityVerdict] = mapped_column(
        Enum(
            QualityVerdict,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="quality_verdict",
        ),
        nullable=False,
    )
    dimensions_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    reason_summary: Mapped[str] = mapped_column(String(2048), nullable=False)
    evidence_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_run_id: Mapped[ModelRunId | None] = mapped_column(
        TypedIdType(ModelRunId),
        ForeignKey("model_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    human_override: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", QualityCheckId), name="quality_check_id_prefix"),
        CheckConstraint("length(policy_version) > 0", name="quality_policy_not_empty"),
        CheckConstraint("json_valid(dimensions_json)", name="quality_dimensions_json_valid"),
        CheckConstraint("length(reason_code) > 0", name="quality_reason_code_not_empty"),
        CheckConstraint("length(reason_summary) > 0", name="quality_reason_not_empty"),
        CheckConstraint(
            sha256_check("evidence_snapshot_hash"),
            name="quality_evidence_snapshot_hash",
        ),
    )


class QualityCheckEvidenceTable(Base):
    __tablename__ = "quality_check_evidence"

    quality_check_id: Mapped[QualityCheckId] = mapped_column(
        TypedIdType(QualityCheckId),
        ForeignKey("quality_checks.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    evidence_id: Mapped[EvidenceId] = mapped_column(
        TypedIdType(EvidenceId),
        ForeignKey("evidence.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint("position >= 0", name="quality_evidence_position_nonnegative"),
        UniqueConstraint(
            "quality_check_id",
            "position",
            name="uq_quality_check_evidence_position",
        ),
    )


__all__ = [
    "ClaimOriginTable",
    "ClaimTable",
    "EvidenceFamilyTable",
    "EvidenceTable",
    "QualityCheckEvidenceTable",
    "QualityCheckTable",
]
