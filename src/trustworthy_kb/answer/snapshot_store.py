"""Private content-addressed storage for replayable verified answers."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from pathlib import Path

from pydantic import ValidationError

from trustworthy_kb.answer.contracts import AnsweredResult
from trustworthy_kb.answer.errors import AnswerIntegrityError
from trustworthy_kb.governance.fingerprints import canonical_json_hash

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AnswerSnapshotStore:
    """Persist private verified results without putting answer text in SQLite."""

    def __init__(self, root: Path, *, max_bytes: int = 2 * 1024 * 1024) -> None:
        if max_bytes < 1024:
            raise ValueError("answer snapshot limit is too small")
        root_input = root.expanduser()
        if root_input.exists() and root_input.is_symlink():
            raise AnswerIntegrityError("answer snapshot root must not be a symlink")
        self._root = root_input.resolve(strict=False)
        self._max_bytes = max_bytes

    async def put(self, result: AnsweredResult) -> str:
        """Store one immutable result and return its canonical hash."""

        return await asyncio.to_thread(self._put_sync, result)

    async def get(self, content_hash: str) -> AnsweredResult:
        return await asyncio.to_thread(self._get_sync, content_hash)

    async def purge_by_chunk_ids(self, chunk_ids: frozenset[str]) -> int:
        """Remove verified answer payloads that cite any invalidated Chunk ID."""

        if not chunk_ids:
            return 0
        return await asyncio.to_thread(self._purge_by_chunk_ids_sync, chunk_ids)

    def _put_sync(self, result: AnsweredResult) -> str:
        payload = result.model_dump(mode="json")
        content_hash = canonical_json_hash(payload)
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > self._max_bytes:
            raise AnswerIntegrityError("verified answer exceeds its snapshot limit")
        target = self._path(content_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_parents(self._root, target)
        if target.exists():
            if target.is_symlink() or target.read_bytes() != raw:
                raise AnswerIntegrityError("answer snapshot write conflict")
            return content_hash
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
            raise AnswerIntegrityError("verified answer snapshot could not be stored") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        return content_hash

    def _get_sync(self, content_hash: str) -> AnsweredResult:
        target = self._path(content_hash)
        _reject_symlink_parents(self._root, target)
        try:
            if target.is_symlink() or not target.is_file():
                raise AnswerIntegrityError("verified answer snapshot is unavailable")
            if target.stat().st_size > self._max_bytes:
                raise AnswerIntegrityError("verified answer exceeds its snapshot limit")
            raw = target.read_bytes()
            result = AnsweredResult.model_validate_json(raw)
            if canonical_json_hash(result.model_dump(mode="json")) != content_hash:
                raise AnswerIntegrityError("verified answer snapshot identity changed")
            return result
        except AnswerIntegrityError:
            raise
        except (OSError, ValidationError, UnicodeDecodeError):
            raise AnswerIntegrityError("verified answer snapshot failed validation") from None

    def _purge_by_chunk_ids_sync(self, chunk_ids: frozenset[str]) -> int:
        sha_root = self._root / "sha256"
        if not sha_root.exists():
            return 0
        if sha_root.is_symlink() or not sha_root.is_dir():
            raise AnswerIntegrityError("answer snapshot hierarchy is unsafe")
        purged = 0
        try:
            prefixes = tuple(sha_root.iterdir())
            for prefix in prefixes:
                if (
                    prefix.is_symlink()
                    or not prefix.is_dir()
                    or not re.fullmatch(r"[0-9a-f]{2}", prefix.name)
                ):
                    raise AnswerIntegrityError("answer snapshot hierarchy is unsafe")
                for target in tuple(prefix.iterdir()):
                    if target.is_symlink() or not target.is_file():
                        raise AnswerIntegrityError("answer snapshot hierarchy is unsafe")
                    content_hash = target.stem
                    if target.suffix != ".json" or not _SHA256.fullmatch(content_hash):
                        raise AnswerIntegrityError("answer snapshot hierarchy is unsafe")
                    result = self._get_sync(content_hash)
                    cited = {citation.chunk_id for citation in result.citations}
                    if cited.intersection(chunk_ids):
                        target.unlink()
                        purged += 1
        except AnswerIntegrityError:
            raise
        except OSError:
            raise AnswerIntegrityError("answer snapshot invalidation failed safely") from None
        return purged

    def _path(self, content_hash: str) -> Path:
        if not _SHA256.fullmatch(content_hash):
            raise AnswerIntegrityError("verified answer snapshot hash is invalid")
        return self._root / "sha256" / content_hash[:2] / f"{content_hash}.json"


def _reject_symlink_parents(root: Path, target: Path) -> None:
    cursor = root
    for part in target.relative_to(root).parts:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise AnswerIntegrityError("answer snapshot path contains a symlink")


__all__ = ["AnswerSnapshotStore"]
