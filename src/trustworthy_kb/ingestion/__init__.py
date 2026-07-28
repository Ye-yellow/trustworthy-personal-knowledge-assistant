"""Deterministic local ingestion interfaces."""

from trustworthy_kb.ingestion.errors import (
    DocumentTooLargeError,
    IngestionAlreadyRunningError,
    IngestionConfigurationError,
    IngestionError,
    MarkdownParseError,
    ObsidianCliUnavailableError,
    ObsidianCommandError,
    SnapshotIntegrityError,
    UnstableFileError,
    UnsupportedEncodingError,
    VaultPathPolicyError,
)

__all__ = [
    "DocumentTooLargeError",
    "IngestionAlreadyRunningError",
    "IngestionConfigurationError",
    "IngestionError",
    "MarkdownParseError",
    "ObsidianCliUnavailableError",
    "ObsidianCommandError",
    "SnapshotIntegrityError",
    "UnstableFileError",
    "UnsupportedEncodingError",
    "VaultPathPolicyError",
]
