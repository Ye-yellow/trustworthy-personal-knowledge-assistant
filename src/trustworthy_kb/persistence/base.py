"""Shared SQLAlchemy metadata and declarative base."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import MetaData, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from trustworthy_kb.domain.enums import EntityType
from trustworthy_kb.domain.ids import TypedId
from trustworthy_kb.persistence.types import UTCDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for internal persistence mappings."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utc_now() -> datetime:
    """Return the current UTC-aware time."""

    return datetime.now(UTC)


_UTC_SERVER_DEFAULT = text("(strftime('%Y-%m-%dT%H:%M:%f000Z','now'))")


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        server_default=_UTC_SERVER_DEFAULT,
        nullable=False,
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
        server_default=_UTC_SERVER_DEFAULT,
        nullable=False,
    )


class RevisionMixin:
    revision: Mapped[int] = mapped_column(
        default=1,
        server_default=text("1"),
        nullable=False,
    )


def id_prefix_check(column: str, id_type: type[TypedId]) -> str:
    """Return a SQLite CHECK expression for a typed ID prefix and length."""

    return f"{column} LIKE '{id_type.prefix}%' AND length({column}) = {len(id_type.prefix) + 26}"


def sha256_check(column: str) -> str:
    """Return a SQLite CHECK expression for lowercase SHA-256 hex."""

    return f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'"


_ENTITY_PREFIXES = {
    EntityType.SOURCE: "source_",
    EntityType.SOURCE_VERSION: "srcver_",
    EntityType.CONTENT_BLOCK: "block_",
    EntityType.CLAIM: "claim_",
    EntityType.EVIDENCE: "evidence_",
    EntityType.QUALITY_CHECK: "qcheck_",
    EntityType.KNOWLEDGE_CHANGE: "change_",
    EntityType.KNOWLEDGE_NOTE: "note_",
    EntityType.CURATED_VERSION: "curated_",
    EntityType.INDEX_GENERATION: "idxgen_",
    EntityType.INDEX_JOB: "idxjob_",
    EntityType.MODEL_RUN: "modelrun_",
}


def entity_id_check(type_column: str, id_column: str) -> str:
    """Return a CHECK expression matching an entity type to its ID prefix."""

    return " OR ".join(
        f"({type_column} = '{entity_type.value}' AND {id_column} LIKE '{prefix}%')"
        for entity_type, prefix in _ENTITY_PREFIXES.items()
    )


__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "CreatedAtMixin",
    "RevisionMixin",
    "TimestampMixin",
    "entity_id_check",
    "id_prefix_check",
    "sha256_check",
    "utc_now",
]
