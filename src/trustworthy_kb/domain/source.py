"""Immutable domain records for source lineage."""

from __future__ import annotations

from trustworthy_kb.domain.base import (
    AwareDatetime,
    DomainRecord,
    NonEmptyText,
    NonNegativeInt,
    Revision,
    Sha256Hex,
)
from trustworthy_kb.domain.enums import Sensitivity, SourceType, SourceVersionStatus, TrustTier
from trustworthy_kb.domain.ids import ContentBlockId, SourceId, SourceVersionId


class SourceRecord(DomainRecord):
    id: SourceId
    source_type: SourceType
    canonical_uri: NonEmptyText
    owner: NonEmptyText
    trust_tier: TrustTier
    sensitivity: Sensitivity
    current_version_id: SourceVersionId | None = None
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    deleted_at: AwareDatetime | None = None


class SourceVersionRecord(DomainRecord):
    id: SourceVersionId
    source_id: SourceId
    version_number: Revision
    content_hash: Sha256Hex
    byte_size: NonNegativeInt
    media_type: NonEmptyText
    captured_at: AwareDatetime
    source_modified_at: AwareDatetime | None = None
    original_path: NonEmptyText
    status: SourceVersionStatus
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ContentBlockRecord(DomainRecord):
    id: ContentBlockId
    source_version_id: SourceVersionId
    ordinal: NonNegativeInt
    block_type: NonEmptyText
    anchor: NonEmptyText
    text_hash: Sha256Hex
    character_count: NonNegativeInt
    created_at: AwareDatetime


__all__ = ["ContentBlockRecord", "SourceRecord", "SourceVersionRecord"]
