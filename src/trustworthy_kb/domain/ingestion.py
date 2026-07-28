"""Immutable records for ingestion runs, items, and source locations."""

from __future__ import annotations

from pydantic import Field

from trustworthy_kb.domain.base import (
    AwareDatetime,
    DomainRecord,
    NonEmptyText,
    NonNegativeInt,
    Revision,
    Sha256Hex,
)
from trustworthy_kb.domain.enums import IngestionAction, IngestionItemStatus, IngestionRunStatus
from trustworthy_kb.domain.ids import IngestionItemId, IngestionRunId, SourceId, SourceVersionId


class SourceLocationRecord(DomainRecord):
    source_id: SourceId
    vault_id_hash: Sha256Hex
    relative_path: NonEmptyText
    path_key: Sha256Hex
    file_key: Sha256Hex | None = None
    last_seen_run_id: IngestionRunId | None = None
    observed_size: NonNegativeInt
    observed_mtime_ns: NonNegativeInt
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    deleted_at: AwareDatetime | None = None


class IngestionRunRecord(DomainRecord):
    id: IngestionRunId
    vault_id_hash: Sha256Hex
    scan_scope_hash: Sha256Hex
    manifest_hash: Sha256Hex
    status: IngestionRunStatus
    total_items: NonNegativeInt = 0
    succeeded_items: NonNegativeInt = 0
    skipped_items: NonNegativeInt = 0
    quarantined_items: NonNegativeInt = 0
    failed_items: NonNegativeInt = 0
    error_category: str | None = None
    revision: Revision
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class IngestionItemRecord(DomainRecord):
    id: IngestionItemId
    run_id: IngestionRunId
    source_id: SourceId | None = None
    action: IngestionAction
    relative_path: NonEmptyText
    path_key: Sha256Hex
    file_key: Sha256Hex | None = None
    content_hash: Sha256Hex | None = None
    base_version_id: SourceVersionId | None = None
    result_version_id: SourceVersionId | None = None
    status: IngestionItemStatus
    operation_id: NonEmptyText
    attempt: Revision = 1
    error_category: str | None = None
    safety_signals_json: dict[str, NonNegativeInt] = Field(default_factory=dict)
    revision: Revision
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None


__all__ = ["IngestionItemRecord", "IngestionRunRecord", "SourceLocationRecord"]
