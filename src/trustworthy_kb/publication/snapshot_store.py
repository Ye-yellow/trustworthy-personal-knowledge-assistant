"""Private content-addressed storage for restart-safe curation artifacts."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from pathlib import Path

from pydantic import ValidationError

from trustworthy_kb.publication.contracts import (
    CurationArtifact,
    CurationClaim,
    PublicationSnapshot,
)
from trustworthy_kb.publication.curation import verify_curated_markdown
from trustworthy_kb.publication.errors import PublicationError

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PublicationSnapshotStore:
    """Persist immutable artifacts outside the user Vault for Saga recovery."""

    def __init__(self, root: Path, *, max_bytes: int = 10 * 1024 * 1024) -> None:
        if max_bytes < 1024:
            raise ValueError("publication snapshot limit is too small")
        root_input = root.expanduser()
        if root_input.exists() and root_input.is_symlink():
            raise PublicationError("curation snapshot root must not be a symlink")
        self._root = root_input.resolve(strict=False)
        self._max_bytes = max_bytes

    async def put(self, artifact: CurationArtifact, claims: tuple[CurationClaim, ...]) -> None:
        await asyncio.to_thread(
            self._put_sync,
            PublicationSnapshot(artifact=artifact, claims=claims),
        )

    async def get(self, content_hash: str) -> PublicationSnapshot:
        return await asyncio.to_thread(self._get_sync, content_hash)

    def _put_sync(self, snapshot: PublicationSnapshot) -> None:
        raw = json.dumps(
            snapshot.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > self._max_bytes:
            raise PublicationError("curation snapshot exceeds its storage limit")
        target = self._path(snapshot.artifact.content_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_parents(self._root, target)
        if target.exists():
            if target.is_symlink() or target.read_bytes() != raw:
                raise PublicationError("curation snapshot write conflict")
            return
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
            raise PublicationError("curation snapshot could not be stored") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def _get_sync(self, content_hash: str) -> PublicationSnapshot:
        target = self._path(content_hash)
        _reject_symlink_parents(self._root, target)
        try:
            if target.is_symlink() or not target.is_file():
                raise PublicationError("curation snapshot is unavailable")
            if target.stat().st_size > self._max_bytes:
                raise PublicationError("curation snapshot exceeds its storage limit")
            raw = target.read_bytes()
            snapshot = PublicationSnapshot.model_validate_json(raw)
            if snapshot.artifact.content_hash != content_hash:
                raise PublicationError("curation snapshot identity changed")
            verify_curated_markdown(snapshot.artifact.markdown, expected_hash=content_hash)
            return snapshot
        except PublicationError:
            raise
        except (OSError, ValidationError, UnicodeDecodeError):
            raise PublicationError("curation snapshot failed integrity validation") from None

    def _path(self, content_hash: str) -> Path:
        if not _SHA256.fullmatch(content_hash):
            raise PublicationError("curation snapshot hash is invalid")
        return self._root / "artifacts" / "sha256" / content_hash[:2] / f"{content_hash}.json"


def _reject_symlink_parents(root: Path, target: Path) -> None:
    cursor = root
    for part in target.relative_to(root).parts:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise PublicationError("curation snapshot path contains a symlink")


__all__ = ["PublicationSnapshotStore"]
