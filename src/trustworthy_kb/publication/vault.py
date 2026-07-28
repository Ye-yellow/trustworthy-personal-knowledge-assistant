"""Constrained atomic filesystem publisher for a local Obsidian Vault."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from pathlib import Path, PurePosixPath

from trustworthy_kb.domain import CuratedVersionId
from trustworthy_kb.publication.contracts import CurationArtifact
from trustworthy_kb.publication.curation import verify_curated_markdown
from trustworthy_kb.publication.errors import CurationError, VaultPublicationError

_SAFE_ROOT = re.compile(r"^[A-Za-z0-9._/-]+$")


class AtomicVaultPublisher:
    """Write only generated Markdown, with path confinement and compare-and-swap."""

    def __init__(
        self,
        vault_root: Path,
        *,
        staging_root: str = "_AI/Staging",
        versions_root: str = "_AI/Versions",
        max_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        root = vault_root.expanduser().resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise VaultPublicationError("Vault root must be a real directory")
        if max_bytes < 1024:
            raise ValueError("Vault publication byte limit is too small")
        self._root = root
        self._staging_root = _root_path(staging_root)
        self._versions_root = _root_path(versions_root)
        self._max_bytes = max_bytes

    def staging_path(self, artifact: CurationArtifact) -> str:
        """Return the canonical Vault-relative staging path."""

        return (
            self._staging_root / str(artifact.note_id) / f"{artifact.curated_version_id}.md"
        ).as_posix()

    async def stage(self, artifact: CurationArtifact) -> str:
        """Atomically write or idempotently reuse one verified staging artifact."""

        return await asyncio.to_thread(self._stage_sync, artifact)

    async def verify(self, relative_path: str, *, expected_hash: str) -> dict[str, object]:
        """Read and verify one generated Markdown file."""

        return await asyncio.to_thread(self._verify_sync, relative_path, expected_hash)

    async def publish(
        self,
        artifact: CurationArtifact,
        final_relative_path: str,
        *,
        expected_current_version_id: CuratedVersionId | None = None,
        expected_current_hash: str | None = None,
    ) -> str:
        """Move a verified staged file to its final path under CAS protection."""

        return await asyncio.to_thread(
            self._publish_sync,
            artifact,
            final_relative_path,
            expected_current_version_id,
            expected_current_hash,
        )

    async def exists(self, relative_path: str) -> bool:
        """Return whether a non-symlink file exists at a safe relative path."""

        return await asyncio.to_thread(self._exists_sync, relative_path)

    def _stage_sync(self, artifact: CurationArtifact) -> str:
        relative = self.staging_path(artifact)
        target = self._target(relative, create_parents=True)
        raw = artifact.markdown.encode("utf-8")
        if len(raw) > self._max_bytes:
            raise VaultPublicationError("curated artifact exceeds the Vault write limit")
        if target.exists():
            metadata = self._verify_target(target, artifact.content_hash)
            if metadata.get("curated_version_id") != str(artifact.curated_version_id):
                raise VaultPublicationError("staging path contains a different curated version")
            return relative
        _atomic_write(target, raw)
        self._verify_target(target, artifact.content_hash)
        return relative

    def _verify_sync(self, relative_path: str, expected_hash: str) -> dict[str, object]:
        target = self._target(relative_path, create_parents=False)
        return self._verify_target(target, expected_hash)

    def _publish_sync(
        self,
        artifact: CurationArtifact,
        final_relative_path: str,
        expected_current_version_id: CuratedVersionId | None,
        expected_current_hash: str | None,
    ) -> str:
        staging = self._target(self.staging_path(artifact), create_parents=False)
        self._verify_target(staging, artifact.content_hash)
        target = self._target(final_relative_path, create_parents=True)
        if target.exists():
            if expected_current_version_id is None or expected_current_hash is None:
                raise VaultPublicationError(
                    "existing final note requires an expected version and hash"
                )
            current = self._verify_target(target, expected_current_hash)
            if current.get("curated_version_id") != str(expected_current_version_id):
                raise VaultPublicationError("final note changed since publication planning")
            archive_relative = (
                self._versions_root / str(artifact.note_id) / f"{expected_current_version_id}.md"
            ).as_posix()
            archive = self._target(archive_relative, create_parents=True)
            current_raw = _safe_read(target, self._max_bytes)
            if archive.exists():
                self._verify_target(archive, expected_current_hash)
            else:
                _atomic_write(archive, current_raw)
                self._verify_target(archive, expected_current_hash)
        try:
            os.replace(staging, target)
        except OSError:
            raise VaultPublicationError("atomic Vault publication failed") from None
        self._verify_target(target, artifact.content_hash)
        return _relative_text(final_relative_path)

    def _exists_sync(self, relative_path: str) -> bool:
        target = self._target(relative_path, create_parents=False)
        return target.exists() and target.is_file() and not target.is_symlink()

    def _verify_target(self, target: Path, expected_hash: str) -> dict[str, object]:
        raw = _safe_read(target, self._max_bytes)
        try:
            text = raw.decode("utf-8")
            metadata = verify_curated_markdown(text, expected_hash=expected_hash)
        except (UnicodeDecodeError, CurationError):
            raise VaultPublicationError("Vault write-back verification failed") from None
        return metadata

    def _target(self, relative_path: str, *, create_parents: bool) -> Path:
        relative = PurePosixPath(_relative_text(relative_path))
        target = self._root.joinpath(*relative.parts)
        if create_parents:
            _create_safe_parents(self._root, target.parent)
        _reject_symlink_chain(self._root, target, allow_missing=True)
        resolved_parent = target.parent.resolve(strict=False)
        candidate = resolved_parent / target.name
        if not candidate.is_relative_to(self._root):
            raise VaultPublicationError("Vault path escapes the configured root")
        return candidate


def _root_path(value: str) -> PurePosixPath:
    text = value.strip()
    path = PurePosixPath(text)
    if (
        not text
        or "\\" in text
        or "\x00" in text
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("generated Vault roots must be normalized and relative")
    if not _SAFE_ROOT.fullmatch(text):
        raise ValueError("generated Vault roots contain unsupported characters")
    return path


def _relative_text(value: str) -> str:
    text = value.strip()
    if not text or "\\" in text or "\x00" in text:
        raise VaultPublicationError("Vault path must be normalized and relative")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise VaultPublicationError("Vault path must be normalized and relative")
    if path.suffix.lower() != ".md":
        raise VaultPublicationError("Vault publisher only writes Markdown files")
    return path.as_posix()


def _create_safe_parents(root: Path, parent: Path) -> None:
    relative = parent.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.exists():
            if cursor.is_symlink() or not cursor.is_dir():
                raise VaultPublicationError("Vault output parent is unsafe")
        else:
            try:
                cursor.mkdir()
            except FileExistsError:
                if cursor.is_symlink() or not cursor.is_dir():
                    raise VaultPublicationError("Vault output parent is unsafe") from None
            except OSError:
                raise VaultPublicationError("Vault output directory could not be created") from None


def _reject_symlink_chain(root: Path, target: Path, *, allow_missing: bool) -> None:
    cursor = root
    for part in target.relative_to(root).parts:
        cursor /= part
        if not cursor.exists():
            if allow_missing:
                continue
            raise VaultPublicationError("Vault output path is unavailable")
        if cursor.is_symlink():
            raise VaultPublicationError("Vault output path contains a symlink")


def _safe_read(target: Path, max_bytes: int) -> bytes:
    try:
        if target.is_symlink() or not target.is_file():
            raise VaultPublicationError("Vault output file is unavailable or unsafe")
        size = target.stat().st_size
        if size > max_bytes:
            raise VaultPublicationError("Vault output file exceeds the read limit")
        raw = target.read_bytes()
    except VaultPublicationError:
        raise
    except OSError:
        raise VaultPublicationError("Vault output file could not be read") from None
    if len(raw) != size:
        raise VaultPublicationError("Vault output changed during verification")
    return raw


def _atomic_write(target: Path, raw: bytes) -> None:
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError:
        raise VaultPublicationError("atomic Vault write failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["AtomicVaultPublisher"]
