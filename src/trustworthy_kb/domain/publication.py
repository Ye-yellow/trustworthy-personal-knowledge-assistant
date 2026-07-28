"""Immutable domain records for curation, lineage, and indexing control."""

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


class KnowledgeChangeRecord(DomainRecord):
    id: KnowledgeChangeId
    source_id: SourceId
    base_version_id: SourceVersionId | None = None
    target_version_id: SourceVersionId
    change_type: ChangeType
    diff_hash: Sha256Hex
    diff_summary_json: DomainJson
    status: KnowledgeChangeStatus
    operation_id: NonEmptyText
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime


class KnowledgeNoteRecord(DomainRecord):
    id: KnowledgeNoteId
    canonical_path: NonEmptyText
    current_curated_version_id: CuratedVersionId | None = None
    active_index_generation_id: IndexGenerationId | None = None
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    deleted_at: AwareDatetime | None = None


class CuratedVersionRecord(DomainRecord):
    id: CuratedVersionId
    note_id: KnowledgeNoteId
    version_number: Revision
    based_on_change_id: KnowledgeChangeId
    content_hash: Sha256Hex
    vault_path: NonEmptyText
    status: CuratedVersionStatus
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LineageEdgeRecord(DomainRecord):
    id: LineageEdgeId
    from_type: EntityType
    from_id: TypedId
    to_type: EntityType
    to_id: TypedId
    relation: NonEmptyText
    operation_id: NonEmptyText
    created_at: AwareDatetime


class IndexGenerationRecord(DomainRecord):
    id: IndexGenerationId
    generation_number: Revision
    embedding_model: NonEmptyText
    chunker_version: NonEmptyText
    status: IndexGenerationStatus
    revision: Revision
    created_at: AwareDatetime
    activated_at: AwareDatetime | None = None


class IndexJobRecord(DomainRecord):
    id: IndexJobId
    object_type: EntityType
    object_id: TypedId
    generation_id: IndexGenerationId
    status: IndexJobStatus
    attempt: NonNegativeInt
    error_category: str | None = None
    lease_owner: str | None = None
    lease_expires_at: AwareDatetime | None = None
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime


__all__ = [
    "CuratedVersionRecord",
    "IndexGenerationRecord",
    "IndexJobRecord",
    "KnowledgeChangeRecord",
    "KnowledgeNoteRecord",
    "LineageEdgeRecord",
]
