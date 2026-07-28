"""Canonical hashing and local source identity helpers."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from trustworthy_kb.ingestion.paths import normalize_vault_relative_path


def sha256_bytes(value: bytes) -> str:
    """Return lowercase SHA-256 for raw bytes."""

    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    """Return lowercase SHA-256 for UTF-8 text."""

    return sha256_bytes(value.encode("utf-8"))


def canonical_json(value: Mapping[str, Any] | Sequence[Any]) -> str:
    """Serialize JSON deterministically without ASCII rewriting."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_json_hash(value: Mapping[str, Any] | Sequence[Any]) -> str:
    """Hash a canonical JSON value."""

    return sha256_text(canonical_json(value))


def vault_id_hash(vault_id: str) -> str:
    """Hash a private Obsidian Vault identifier."""

    normalized = vault_id.strip()
    if not normalized:
        raise ValueError("Vault ID must not be empty")
    return sha256_text(normalized)


def path_key(relative_path: str) -> str:
    """Return a Windows-safe identity hash for a normalized Vault path."""

    normalized = normalize_vault_relative_path(relative_path)
    return sha256_text(unicodedata.normalize("NFC", normalized).casefold())


def file_key(device: int, inode: int) -> str | None:
    """Hash a reliable filesystem identity without storing raw device values."""

    if device < 0 or inode <= 0:
        return None
    return sha256_text(f"{device}:{inode}")


def canonical_source_uri(vault_hash: str, relative_path: str) -> str:
    """Build the private canonical URI for a validated source location."""

    if len(vault_hash) != 64 or any(
        character not in "0123456789abcdef" for character in vault_hash
    ):
        raise ValueError("Vault hash must be lowercase SHA-256")
    normalized = normalize_vault_relative_path(relative_path)
    encoded_path = "/".join(quote(part, safe="") for part in normalized.split("/"))
    return f"obsidian://vault/{vault_hash}/{encoded_path}"


__all__ = [
    "canonical_json",
    "canonical_json_hash",
    "canonical_source_uri",
    "file_key",
    "path_key",
    "sha256_bytes",
    "sha256_text",
    "vault_id_hash",
]
