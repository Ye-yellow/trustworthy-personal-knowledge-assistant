"""Fail-closed policy for Vault-relative Markdown paths."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from trustworthy_kb.ingestion.errors import VaultPathPolicyError

_WINDOWS_DEVICE_NAME = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def normalize_vault_relative_path(value: str) -> str:
    """Normalize one Markdown path without revealing it in failures."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise VaultPathPolicyError("path policy rejected input")
    normalized = unicodedata.normalize("NFC", value.replace("\\", "/"))
    if (
        normalized.startswith("/")
        or normalized.startswith("//")
        or _WINDOWS_DRIVE.match(normalized)
    ):
        raise VaultPathPolicyError("path policy rejected input")
    parts = normalized.split("/")
    if any(
        part in {"", ".", ".."}
        or part.rstrip(" .") != part
        or ":" in part
        or _WINDOWS_DEVICE_NAME.match(part)
        for part in parts
    ):
        raise VaultPathPolicyError("path policy rejected input")
    result = "/".join(parts)
    if not result.casefold().endswith(".md"):
        raise VaultPathPolicyError("path policy rejected input")
    return result


def resolve_vault_markdown(vault_root: Path, relative_path: str) -> tuple[str, Path]:
    """Resolve a Markdown path and reject containment or symlink escapes."""

    normalized = normalize_vault_relative_path(relative_path)
    try:
        root = vault_root.resolve(strict=True)
    except OSError as error:
        raise VaultPathPolicyError("Vault root is unavailable") from error
    if not root.is_dir():
        raise VaultPathPolicyError("Vault root is unavailable")

    candidate = root.joinpath(*normalized.split("/"))
    current = root
    for part in normalized.split("/"):
        current = current / part
        if current.is_symlink():
            raise VaultPathPolicyError("path policy rejected symbolic link")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise VaultPathPolicyError("Vault Markdown is unavailable") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise VaultPathPolicyError("path policy rejected input")
    return normalized, resolved


def path_is_in_scope(
    relative_path: str,
    *,
    allowed_roots: tuple[str, ...],
    excluded_roots: tuple[str, ...],
) -> bool:
    """Return whether a normalized Markdown path belongs to the scan scope."""

    normalized = normalize_vault_relative_path(relative_path)
    folded = normalized.casefold()

    def contains(root: str) -> bool:
        if root == ".":
            return True
        prefix = unicodedata.normalize("NFC", root).strip("/").casefold()
        return folded == prefix or folded.startswith(f"{prefix}/")

    return any(contains(root) for root in allowed_roots) and not any(
        contains(root) for root in excluded_roots
    )


__all__ = ["normalize_vault_relative_path", "path_is_in_scope", "resolve_vault_markdown"]
