"""Frozen internal data exchanged by deterministic ingestion components."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IngestionValue(BaseModel):
    """Strict immutable base for ingestion values."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class VaultFileObservation(IngestionValue):
    relative_path: str = Field(min_length=1)
    path_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    mtime_ns: int = Field(ge=0)
    file_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class StableDocument(IngestionValue):
    observation: VaultFileObservation
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_bytes: bytes


class SnapshotRef(IngestionValue):
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)


class ParsedBlock(IngestionValue):
    ordinal: int = Field(ge=0)
    block_type: str = Field(min_length=1)
    anchor: str = Field(min_length=1)
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    character_count: int = Field(ge=0)
    text: str


class ParsedDocument(IngestionValue):
    blocks: tuple[ParsedBlock, ...]


__all__ = [
    "IngestionValue",
    "ParsedBlock",
    "ParsedDocument",
    "SnapshotRef",
    "StableDocument",
    "VaultFileObservation",
]
