"""Content-addressed immutable snapshot storage."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from trustworthy_kb.ingestion.errors import SnapshotIntegrityError
from trustworthy_kb.ingestion.hashing import sha256_bytes
from trustworthy_kb.ingestion.types import SnapshotRef


class ContentAddressedSnapshotStore:
    """Write and verify immutable raw Markdown snapshots outside the Vault."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def put(self, raw_bytes: bytes, content_hash: str) -> SnapshotRef:
        """Persist raw bytes once and return a path-free reference."""

        if sha256_bytes(raw_bytes) != content_hash:
            raise SnapshotIntegrityError("snapshot input hash does not match content")
        await asyncio.to_thread(self._put_sync, raw_bytes, content_hash)
        return SnapshotRef(content_hash=content_hash, byte_size=len(raw_bytes))

    def _put_sync(self, raw_bytes: bytes, content_hash: str) -> None:
        target = self._target(content_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._verify_existing(target, content_hash)
            return

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".snapshot-",
            suffix=".part",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                self._verify_existing(target, content_hash)
            except OSError as error:
                raise SnapshotIntegrityError(
                    "snapshot could not be committed atomically"
                ) from error
            self._fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _target(self, content_hash: str) -> Path:
        if len(content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in content_hash
        ):
            raise SnapshotIntegrityError("snapshot hash is invalid")
        return self._root / "sha256" / content_hash[:2] / f"{content_hash[2:]}.md"

    @staticmethod
    def _verify_existing(target: Path, content_hash: str) -> None:
        try:
            existing_hash = sha256_bytes(target.read_bytes())
        except OSError as error:
            raise SnapshotIntegrityError("snapshot could not be verified") from error
        if existing_hash != content_hash:
            raise SnapshotIntegrityError("existing snapshot failed integrity verification")

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = ["ContentAddressedSnapshotStore"]
