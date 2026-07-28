"""Deterministic full-scan manifests without Markdown content."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, model_validator

from trustworthy_kb.ingestion.errors import IngestionError
from trustworthy_kb.ingestion.hashing import canonical_json_hash, path_key
from trustworthy_kb.ingestion.types import IngestionValue, StableDocument, VaultFileObservation


class ManifestEntry(IngestionValue):
    relative_path: str = Field(min_length=1)
    path_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    mtime_ns: int = Field(ge=0)
    file_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_category: str | None = None

    @model_validator(mode="after")
    def _validate_capture_result(self) -> ManifestEntry:
        if (self.content_hash is None) == (self.error_category is None):
            raise ValueError("manifest entry must contain content hash or error category")
        if self.error_category is not None and not self.error_category.strip():
            raise ValueError("manifest error category must not be empty")
        return self

    @classmethod
    def captured(cls, document: StableDocument) -> ManifestEntry:
        """Build an entry from a stable raw capture."""

        observation = document.observation
        return cls(
            relative_path=observation.relative_path,
            path_key=observation.path_key,
            size=observation.size,
            mtime_ns=observation.mtime_ns,
            file_key=observation.file_key,
            content_hash=document.content_hash,
        )

    @classmethod
    def failed(
        cls,
        observation: VaultFileObservation,
        error_category: str,
    ) -> ManifestEntry:
        """Build a path-preserving capture failure entry."""

        return cls(
            relative_path=observation.relative_path,
            path_key=observation.path_key,
            size=observation.size,
            mtime_ns=observation.mtime_ns,
            file_key=observation.file_key,
            error_category=error_category,
        )


class IngestionManifest(IngestionValue):
    complete: bool
    entries: tuple[ManifestEntry, ...]
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def build_manifest(
    entries: Sequence[ManifestEntry],
    *,
    complete: bool,
) -> IngestionManifest:
    """Validate, sort, and hash a full-scan manifest."""

    ordered = tuple(sorted(entries, key=lambda entry: entry.path_key))
    seen: set[str] = set()
    for entry in ordered:
        if entry.path_key != path_key(entry.relative_path) or entry.path_key in seen:
            raise IngestionError("manifest contains an invalid or duplicate path identity")
        seen.add(entry.path_key)
    manifest_value = [
        {
            "content_hash": entry.content_hash,
            "error_category": entry.error_category,
            "path_key": entry.path_key,
            "size": entry.size,
        }
        for entry in ordered
    ]
    return IngestionManifest(
        complete=complete,
        entries=ordered,
        manifest_hash=canonical_json_hash(manifest_value),
    )


__all__ = ["IngestionManifest", "ManifestEntry", "build_manifest"]
