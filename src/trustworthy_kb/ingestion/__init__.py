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
from trustworthy_kb.ingestion.hashing import (
    canonical_json,
    canonical_json_hash,
    canonical_source_uri,
    file_key,
    path_key,
    sha256_bytes,
    sha256_text,
    vault_id_hash,
)
from trustworthy_kb.ingestion.paths import (
    normalize_vault_relative_path,
    path_is_in_scope,
    resolve_vault_markdown,
)
from trustworthy_kb.ingestion.reader import StableMarkdownReader, decode_markdown
from trustworthy_kb.ingestion.snapshots import ContentAddressedSnapshotStore
from trustworthy_kb.ingestion.types import SnapshotRef, StableDocument, VaultFileObservation

__all__ = [
    "ContentAddressedSnapshotStore",
    "DocumentTooLargeError",
    "IngestionAlreadyRunningError",
    "IngestionConfigurationError",
    "IngestionError",
    "MarkdownParseError",
    "ObsidianCliUnavailableError",
    "ObsidianCommandError",
    "SnapshotIntegrityError",
    "SnapshotRef",
    "StableDocument",
    "StableMarkdownReader",
    "UnstableFileError",
    "UnsupportedEncodingError",
    "VaultFileObservation",
    "VaultPathPolicyError",
    "canonical_json",
    "canonical_json_hash",
    "canonical_source_uri",
    "decode_markdown",
    "file_key",
    "normalize_vault_relative_path",
    "path_is_in_scope",
    "path_key",
    "resolve_vault_markdown",
    "sha256_bytes",
    "sha256_text",
    "vault_id_hash",
]
