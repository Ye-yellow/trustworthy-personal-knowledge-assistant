"""Internal SQLAlchemy mappings for ingestion runs and source locations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from trustworthy_kb.domain.enums import IngestionAction, IngestionItemStatus, IngestionRunStatus
from trustworthy_kb.domain.ids import (
    IngestionItemId,
    IngestionRunId,
    SourceId,
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


class IngestionRunTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[IngestionRunId] = mapped_column(TypedIdType(IngestionRunId), primary_key=True)
    vault_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[IngestionRunStatus] = mapped_column(
        Enum(
            IngestionRunStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="ingestion_run_status",
        ),
        nullable=False,
        default=IngestionRunStatus.PLANNING,
        server_default=IngestionRunStatus.PLANNING.value,
    )
    total_items: Mapped[int] = mapped_column(default=0, server_default=text("0"), nullable=False)
    succeeded_items: Mapped[int] = mapped_column(
        default=0, server_default=text("0"), nullable=False
    )
    skipped_items: Mapped[int] = mapped_column(default=0, server_default=text("0"), nullable=False)
    quarantined_items: Mapped[int] = mapped_column(
        default=0, server_default=text("0"), nullable=False
    )
    failed_items: Mapped[int] = mapped_column(default=0, server_default=text("0"), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%f000Z','now'))"),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", IngestionRunId), name="ingestion_run_id_prefix"),
        CheckConstraint(sha256_check("vault_id_hash"), name="ingestion_run_vault_hash"),
        CheckConstraint(sha256_check("scan_scope_hash"), name="ingestion_run_scope_hash"),
        CheckConstraint(sha256_check("manifest_hash"), name="ingestion_run_manifest_hash"),
        CheckConstraint(
            "total_items >= 0 AND succeeded_items >= 0 AND skipped_items >= 0 "
            "AND quarantined_items >= 0 AND failed_items >= 0",
            name="ingestion_run_counts_nonnegative",
        ),
        CheckConstraint(
            "succeeded_items + skipped_items + quarantined_items + failed_items <= total_items",
            name="ingestion_run_counts_bounded",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ingestion_run_completed_after_start",
        ),
        CheckConstraint("revision >= 1", name="ingestion_run_revision_positive"),
        Index(
            "uq_ingestion_runs_one_active",
            "vault_id_hash",
            unique=True,
            sqlite_where=status.in_((IngestionRunStatus.PLANNING, IngestionRunStatus.APPLYING)),
        ),
    )


class SourceLocationTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "source_locations"

    source_id: Mapped[SourceId] = mapped_column(
        TypedIdType(SourceId),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    vault_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    path_key: Mapped[str] = mapped_column(String(64), nullable=False)
    file_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_run_id: Mapped[IngestionRunId | None] = mapped_column(
        TypedIdType(IngestionRunId),
        ForeignKey("ingestion_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    observed_size: Mapped[int] = mapped_column(nullable=False)
    observed_mtime_ns: Mapped[int] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(sha256_check("vault_id_hash"), name="source_location_vault_hash"),
        CheckConstraint("length(relative_path) > 0", name="source_location_path_not_empty"),
        CheckConstraint(sha256_check("path_key"), name="source_location_path_key"),
        CheckConstraint(
            "file_key IS NULL OR (length(file_key) = 64 AND file_key NOT GLOB '*[^0-9a-f]*')",
            name="source_location_file_key",
        ),
        CheckConstraint(
            "observed_size >= 0 AND observed_mtime_ns >= 0",
            name="source_location_observation_nonnegative",
        ),
        CheckConstraint("revision >= 1", name="source_location_revision_positive"),
        Index("ix_source_locations_vault_id_hash", "vault_id_hash"),
        Index("ix_source_locations_file_key", "file_key"),
        Index(
            "uq_source_locations_live_path",
            "vault_id_hash",
            "path_key",
            unique=True,
            sqlite_where=deleted_at.is_(None),
        ),
    )


class IngestionItemTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "ingestion_items"

    id: Mapped[IngestionItemId] = mapped_column(TypedIdType(IngestionItemId), primary_key=True)
    run_id: Mapped[IngestionRunId] = mapped_column(
        TypedIdType(IngestionRunId),
        ForeignKey("ingestion_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_id: Mapped[SourceId | None] = mapped_column(
        TypedIdType(SourceId), ForeignKey("sources.id", ondelete="RESTRICT"), nullable=True
    )
    action: Mapped[IngestionAction] = mapped_column(
        Enum(
            IngestionAction,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="ingestion_action",
        ),
        nullable=False,
    )
    relative_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    path_key: Mapped[str] = mapped_column(String(64), nullable=False)
    file_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_version_id: Mapped[SourceVersionId | None] = mapped_column(
        TypedIdType(SourceVersionId),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    result_version_id: Mapped[SourceVersionId | None] = mapped_column(
        TypedIdType(SourceVersionId),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[IngestionItemStatus] = mapped_column(
        Enum(
            IngestionItemStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="ingestion_item_status",
        ),
        nullable=False,
        default=IngestionItemStatus.PENDING,
        server_default=IngestionItemStatus.PENDING.value,
    )
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt: Mapped[int] = mapped_column(default=1, server_default=text("1"), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safety_signals_json: Mapped[dict[str, int]] = mapped_column(
        JSON, default=dict, server_default=text("'{}'"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", IngestionItemId), name="ingestion_item_id_prefix"),
        CheckConstraint("length(relative_path) > 0", name="ingestion_item_path_not_empty"),
        CheckConstraint(sha256_check("path_key"), name="ingestion_item_path_key"),
        CheckConstraint(
            "file_key IS NULL OR (length(file_key) = 64 AND file_key NOT GLOB '*[^0-9a-f]*')",
            name="ingestion_item_file_key",
        ),
        CheckConstraint(
            "content_hash IS NULL OR (length(content_hash) = 64 "
            "AND content_hash NOT GLOB '*[^0-9a-f]*')",
            name="ingestion_item_content_hash",
        ),
        CheckConstraint("length(operation_id) > 0", name="ingestion_item_operation_not_empty"),
        CheckConstraint("attempt >= 1", name="ingestion_item_attempt_positive"),
        CheckConstraint(
            "json_valid(safety_signals_json)", name="ingestion_item_signals_json_valid"
        ),
        CheckConstraint("revision >= 1", name="ingestion_item_revision_positive"),
        UniqueConstraint("run_id", "path_key", "action", name="uq_ingestion_items_run_path_action"),
        UniqueConstraint("operation_id", name="uq_ingestion_items_operation"),
        Index("ix_ingestion_items_run_id", "run_id"),
        Index("ix_ingestion_items_run_status", "run_id", "status"),
    )


__all__ = ["IngestionItemTable", "IngestionRunTable", "SourceLocationTable"]
