"""Deterministic local ingestion interfaces."""

from trustworthy_kb.ingestion.diff import StructuralDiff, structural_diff
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
from trustworthy_kb.ingestion.markdown import MarkdownBlockParser, normalize_block_text
from trustworthy_kb.ingestion.paths import (
    normalize_vault_relative_path,
    path_is_in_scope,
    resolve_vault_markdown,
)
from trustworthy_kb.ingestion.reader import StableMarkdownReader, decode_markdown
from trustworthy_kb.ingestion.safety import (
    DocumentSafetyScanner,
    SafetyCategory,
    SafetyReport,
    SafetySeverity,
    SafetySignal,
)
from trustworthy_kb.ingestion.snapshots import ContentAddressedSnapshotStore
from trustworthy_kb.ingestion.types import (
    ParsedBlock,
    ParsedDocument,
    SnapshotRef,
    StableDocument,
    VaultFileObservation,
)

__all__ = [
    "ContentAddressedSnapshotStore",
    "DocumentSafetyScanner",
    "DocumentTooLargeError",
    "IngestionAlreadyRunningError",
    "IngestionConfigurationError",
    "IngestionError",
    "MarkdownBlockParser",
    "MarkdownParseError",
    "ObsidianCliUnavailableError",
    "ObsidianCommandError",
    "ParsedBlock",
    "ParsedDocument",
    "SafetyCategory",
    "SafetyReport",
    "SafetySeverity",
    "SafetySignal",
    "SnapshotIntegrityError",
    "SnapshotRef",
    "StableDocument",
    "StableMarkdownReader",
    "StructuralDiff",
    "UnstableFileError",
    "UnsupportedEncodingError",
    "VaultFileObservation",
    "VaultPathPolicyError",
    "canonical_json",
    "canonical_json_hash",
    "canonical_source_uri",
    "decode_markdown",
    "file_key",
    "normalize_block_text",
    "normalize_vault_relative_path",
    "path_is_in_scope",
    "path_key",
    "resolve_vault_markdown",
    "sha256_bytes",
    "sha256_text",
    "structural_diff",
    "vault_id_hash",
]
