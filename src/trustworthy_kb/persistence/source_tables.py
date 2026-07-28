"""Internal SQLAlchemy mappings for source lineage."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trustworthy_kb.domain.enums import Sensitivity, SourceType, SourceVersionStatus, TrustTier
from trustworthy_kb.domain.ids import ContentBlockId, SourceId, SourceVersionId
from trustworthy_kb.persistence.base import (
    Base,
    CreatedAtMixin,
    RevisionMixin,
    TimestampMixin,
    id_prefix_check,
    sha256_check,
)
from trustworthy_kb.persistence.types import TypedIdType, UTCDateTime


class SourceTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[SourceId] = mapped_column(TypedIdType(SourceId), primary_key=True)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="source_type",
        ),
        nullable=False,
    )
    canonical_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    trust_tier: Mapped[TrustTier] = mapped_column(
        Enum(
            TrustTier,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="trust_tier",
        ),
        nullable=False,
    )
    sensitivity: Mapped[Sensitivity] = mapped_column(
        Enum(
            Sensitivity,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="sensitivity",
        ),
        nullable=False,
    )
    current_version_id: Mapped[SourceVersionId | None] = mapped_column(
        TypedIdType(SourceVersionId),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", SourceId), name="source_id_prefix"),
        CheckConstraint("length(canonical_uri) > 0", name="source_uri_not_empty"),
        CheckConstraint("length(owner) > 0", name="source_owner_not_empty"),
        CheckConstraint("revision >= 1", name="source_revision_positive"),
        Index(
            "uq_sources_live_identity",
            "source_type",
            "canonical_uri",
            "owner",
            unique=True,
            sqlite_where=deleted_at.is_(None),
        ),
    )


class SourceVersionTable(RevisionMixin, TimestampMixin, Base):
    __tablename__ = "source_versions"

    id: Mapped[SourceVersionId] = mapped_column(TypedIdType(SourceVersionId), primary_key=True)
    source_id: Mapped[SourceId] = mapped_column(
        TypedIdType(SourceId),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_modified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    original_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[SourceVersionStatus] = mapped_column(
        Enum(
            SourceVersionStatus,
            values_callable=lambda enum: [item.value for item in enum],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="source_version_status",
        ),
        nullable=False,
        default=SourceVersionStatus.CAPTURED,
        server_default=SourceVersionStatus.CAPTURED.value,
    )

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", SourceVersionId), name="source_version_id_prefix"),
        CheckConstraint("version_number >= 1", name="source_version_number_positive"),
        CheckConstraint(sha256_check("content_hash"), name="source_version_content_hash"),
        CheckConstraint("byte_size >= 0", name="source_version_byte_size_nonnegative"),
        CheckConstraint("length(media_type) > 0", name="source_version_media_type_not_empty"),
        CheckConstraint("length(original_path) > 0", name="source_version_path_not_empty"),
        CheckConstraint("revision >= 1", name="source_version_revision_positive"),
        UniqueConstraint("source_id", "version_number", name="uq_source_versions_number"),
        UniqueConstraint("source_id", "content_hash", name="uq_source_versions_content"),
    )


class ContentBlockTable(CreatedAtMixin, Base):
    __tablename__ = "content_blocks"

    id: Mapped[ContentBlockId] = mapped_column(TypedIdType(ContentBlockId), primary_key=True)
    source_version_id: Mapped[SourceVersionId] = mapped_column(
        TypedIdType(SourceVersionId),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    block_type: Mapped[str] = mapped_column(String(100), nullable=False)
    anchor: Mapped[str] = mapped_column(String(1024), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    character_count: Mapped[int] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint(id_prefix_check("id", ContentBlockId), name="content_block_id_prefix"),
        CheckConstraint("ordinal >= 0", name="content_block_ordinal_nonnegative"),
        CheckConstraint("length(block_type) > 0", name="content_block_type_not_empty"),
        CheckConstraint("length(anchor) > 0", name="content_block_anchor_not_empty"),
        CheckConstraint(sha256_check("text_hash"), name="content_block_text_hash"),
        CheckConstraint(
            "character_count >= 0",
            name="content_block_character_count_nonnegative",
        ),
        UniqueConstraint("source_version_id", "ordinal", name="uq_content_blocks_ordinal"),
        UniqueConstraint("source_version_id", "anchor", name="uq_content_blocks_anchor"),
    )


__all__ = ["ContentBlockTable", "SourceTable", "SourceVersionTable"]
