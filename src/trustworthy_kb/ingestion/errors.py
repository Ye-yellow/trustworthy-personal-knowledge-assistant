"""Safe public errors for local ingestion boundaries."""

from __future__ import annotations


class IngestionError(RuntimeError):
    """Base error for ingestion failures with redacted messages."""


class IngestionConfigurationError(IngestionError):
    """Ingestion configuration is unsafe or incomplete."""


class ObsidianCliUnavailableError(IngestionError):
    """The configured Obsidian executable is unavailable."""


class ObsidianCommandError(IngestionError):
    """An Obsidian command failed without exposing command output."""


class VaultPathPolicyError(IngestionError):
    """A Vault-relative path violated the read policy."""


class UnstableFileError(IngestionError):
    """A file changed repeatedly while it was being captured."""


class DocumentTooLargeError(IngestionError):
    """A Markdown file exceeded the configured byte limit."""


class UnsupportedEncodingError(IngestionError):
    """A Markdown snapshot is not valid UTF-8 or UTF-8 BOM."""


class MarkdownParseError(IngestionError):
    """Markdown structure could not be parsed deterministically."""


class SnapshotIntegrityError(IngestionError):
    """A content-addressed snapshot failed integrity verification."""


class IngestionAlreadyRunningError(IngestionError):
    """A non-terminal run already exists for this Vault."""


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
