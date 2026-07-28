"""Internal SQLAlchemy mappings for curation, lineage, and indexing control."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trustworthy_kb.domain.enums import (
    ChangeType,
    CuratedVersionStatus,
    EntityType,
    IndexGenerationStatus,
    IndexJobStatus,
    KnowledgeChangeStatus,
)
from trustworthy_kb.domain.ids import (
    CuratedVersionId,
    IndexGenerationId,
    IndexJobId,
    KnowledgeChangeId,
    KnowledgeNoteId,
    LineageEdgeId,
    SourceId,
    SourceVersionId,
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


class KnowledgeChangeTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_changes"

    id: Mapped[KnowledgeChangeId] = mapped_column(
        TypedIdType(KnowledgeChangeId),
        primary_key=True,
    )
    source_id: Mapped[SourceId] = mapped_column(
        TypedIdType(SourceId),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    base_version_id: Mapped[SourceVersionId | None] = mapped_column(
        TypedIdType(SourceVersionId),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    target_version_id: Mapped[SourceVersionId] = mapped_column(
        TypedIdType(SourceVersionId),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    change_type: Mapped[ChangeType] = mapped_column(
        Enum(
            ChangeType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="change_type",
        ),
        nullable=False,
    )
    diff_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[KnowledgeChangeStatus] = mapped_column(
        Enum(
            KnowledgeChangeStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="knowledge_change_status",
        ),
        nullable=False,
        default=KnowledgeChangeStatus.RECEIVED,
        server_default=KnowledgeChangeStatus.RECEIVED.value,
    )
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint(
            id_prefix_check("id", KnowledgeChangeId),
            name="knowledge_change_id_prefix",
        ),
        CheckConstraint(sha256_check("diff_hash"), name="knowledge_change_diff_hash"),
        CheckConstraint(
            "json_valid(diff_summary_json)",
            name="knowledge_change_diff_json_valid",
        ),
        CheckConstraint("length(operation_id) > 0", name="knowledge_change_operation_not_empty"),
        CheckConstraint("revision >= 1", name="knowledge_change_revision_positive"),
    )


class KnowledgeNoteTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_notes"

    id: Mapped[KnowledgeNoteId] = mapped_column(TypedIdType(KnowledgeNoteId), primary_key=True)
    canonical_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    current_curated_version_id: Mapped[CuratedVersionId | None] = mapped_column(
        TypedIdType(CuratedVersionId),
        ForeignKey("curated_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    active_index_generation_id: Mapped[IndexGenerationId | None] = mapped_column(
        TypedIdType(IndexGenerationId),
        ForeignKey("index_generations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", KnowledgeNoteId), name="knowledge_note_id_prefix"),
        CheckConstraint("length(canonical_path) > 0", name="knowledge_note_path_not_empty"),
        CheckConstraint("revision >= 1", name="knowledge_note_revision_positive"),
        Index(
            "uq_knowledge_notes_live_path",
            "canonical_path",
            unique=True,
            sqlite_where=deleted_at.is_(None),
        ),
    )


class CuratedVersionTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "curated_versions"

    id: Mapped[CuratedVersionId] = mapped_column(TypedIdType(CuratedVersionId), primary_key=True)
    note_id: Mapped[KnowledgeNoteId] = mapped_column(
        TypedIdType(KnowledgeNoteId),
        ForeignKey("knowledge_notes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    based_on_change_id: Mapped[KnowledgeChangeId] = mapped_column(
        TypedIdType(KnowledgeChangeId),
        ForeignKey("knowledge_changes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    vault_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[CuratedVersionStatus] = mapped_column(
        Enum(
            CuratedVersionStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="curated_version_status",
        ),
        nullable=False,
        default=CuratedVersionStatus.DRAFT,
        server_default=CuratedVersionStatus.DRAFT.value,
    )

    __table_args__ = (
        CheckConstraint(
            id_prefix_check("id", CuratedVersionId),
            name="curated_version_id_prefix",
        ),
        CheckConstraint("version_number >= 1", name="curated_version_number_positive"),
        CheckConstraint(sha256_check("content_hash"), name="curated_version_content_hash"),
        CheckConstraint("length(vault_path) > 0", name="curated_version_path_not_empty"),
        CheckConstraint("revision >= 1", name="curated_version_revision_positive"),
        UniqueConstraint("note_id", "version_number", name="uq_curated_versions_number"),
        UniqueConstraint("note_id", "content_hash", name="uq_curated_versions_content"),
    )


class LineageEdgeTable(CreatedAtMixin, Base):
    __tablename__ = "lineage_edges"

    id: Mapped[LineageEdgeId] = mapped_column(TypedIdType(LineageEdgeId), primary_key=True)
    from_type: Mapped[EntityType] = mapped_column(
        Enum(
            EntityType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="lineage_from_type",
        ),
        nullable=False,
    )
    from_id: Mapped[TypedId] = mapped_column(AnyTypedIdType(), nullable=False, index=True)
    to_type: Mapped[EntityType] = mapped_column(
        Enum(
            EntityType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="lineage_to_type",
        ),
        nullable=False,
    )
    to_id: Mapped[TypedId] = mapped_column(AnyTypedIdType(), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(String(100), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", LineageEdgeId), name="lineage_edge_id_prefix"),
        CheckConstraint(entity_id_check("from_type", "from_id"), name="lineage_from_id_type"),
        CheckConstraint(entity_id_check("to_type", "to_id"), name="lineage_to_id_type"),
        CheckConstraint("length(relation) > 0", name="lineage_relation_not_empty"),
        CheckConstraint("length(operation_id) > 0", name="lineage_operation_not_empty"),
        UniqueConstraint(
            "from_type",
            "from_id",
            "to_type",
            "to_id",
            "relation",
            name="uq_lineage_edges_relation",
        ),
    )


class IndexGenerationTable(RevisionMixin, CreatedAtMixin, Base):
    __tablename__ = "index_generations"

    id: Mapped[IndexGenerationId] = mapped_column(
        TypedIdType(IndexGenerationId),
        primary_key=True,
    )
    generation_number: Mapped[int] = mapped_column(nullable=False, unique=True)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[IndexGenerationStatus] = mapped_column(
        Enum(
            IndexGenerationStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="index_generation_status",
        ),
        nullable=False,
        default=IndexGenerationStatus.STAGING,
        server_default=IndexGenerationStatus.STAGING.value,
    )
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(
            id_prefix_check("id", IndexGenerationId),
            name="index_generation_id_prefix",
        ),
        CheckConstraint("generation_number >= 1", name="index_generation_number_positive"),
        CheckConstraint(
            "length(embedding_model) > 0",
            name="index_generation_embedding_model_not_empty",
        ),
        CheckConstraint(
            "length(chunker_version) > 0",
            name="index_generation_chunker_version_not_empty",
        ),
        CheckConstraint("revision >= 1", name="index_generation_revision_positive"),
        Index(
            "uq_index_generations_one_active",
            "status",
            unique=True,
            sqlite_where=status == IndexGenerationStatus.ACTIVE,
        ),
    )


class IndexJobTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "index_jobs"

    id: Mapped[IndexJobId] = mapped_column(TypedIdType(IndexJobId), primary_key=True)
    object_type: Mapped[EntityType] = mapped_column(
        Enum(
            EntityType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="index_job_object_type",
        ),
        nullable=False,
    )
    object_id: Mapped[TypedId] = mapped_column(AnyTypedIdType(), nullable=False, index=True)
    generation_id: Mapped[IndexGenerationId] = mapped_column(
        TypedIdType(IndexGenerationId),
        ForeignKey("index_generations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[IndexJobStatus] = mapped_column(
        Enum(
            IndexJobStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="index_job_status",
        ),
        nullable=False,
        default=IndexJobStatus.PENDING,
        server_default=IndexJobStatus.PENDING.value,
    )
    attempt: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", IndexJobId), name="index_job_id_prefix"),
        CheckConstraint(entity_id_check("object_type", "object_id"), name="index_job_id_type"),
        CheckConstraint("attempt >= 0", name="index_job_attempt_nonnegative"),
        CheckConstraint("revision >= 1", name="index_job_revision_positive"),
        UniqueConstraint(
            "object_type",
            "object_id",
            "generation_id",
            name="uq_index_jobs_object_generation",
        ),
    )


__all__ = [
    "CuratedVersionTable",
    "IndexGenerationTable",
    "IndexJobTable",
    "KnowledgeChangeTable",
    "KnowledgeNoteTable",
    "LineageEdgeTable",
]
