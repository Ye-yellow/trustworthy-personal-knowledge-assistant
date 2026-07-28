"""Stable, read-only capture of local Markdown bytes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from trustworthy_kb.ingestion.errors import (
    DocumentTooLargeError,
    UnstableFileError,
    UnsupportedEncodingError,
)
from trustworthy_kb.ingestion.hashing import file_key, path_key, sha256_bytes
from trustworthy_kb.ingestion.paths import resolve_vault_markdown
from trustworthy_kb.ingestion.types import StableDocument, VaultFileObservation

type StatProvider = Callable[[Path], os.stat_result]
type BytesProvider = Callable[[Path], bytes]
type SleepProvider = Callable[[float], Awaitable[None]]


class StableMarkdownReader:
    """Capture a file only when metadata stays stable around the read."""

    def __init__(
        self,
        vault_root: Path,
        *,
        max_bytes: int,
        attempts: int = 3,
        interval_ms: int = 250,
        stat_provider: StatProvider | None = None,
        bytes_provider: BytesProvider | None = None,
        sleep_provider: SleepProvider = asyncio.sleep,
    ) -> None:
        if max_bytes < 1 or attempts < 1 or interval_ms < 0:
            raise ValueError("stable reader limits must be positive")
        self._vault_root = vault_root
        self._max_bytes = max_bytes
        self._attempts = attempts
        self._interval_seconds = interval_ms / 1000
        self._stat_provider = stat_provider or _stat
        self._bytes_provider = bytes_provider or _read_bytes
        self._sleep_provider = sleep_provider

    async def read(self, relative_path: str) -> StableDocument:
        """Read stable bytes or raise a redacted deterministic error."""

        normalized, resolved = resolve_vault_markdown(self._vault_root, relative_path)
        for attempt in range(self._attempts):
            before = await asyncio.to_thread(self._stat_provider, resolved)
            if before.st_size > self._max_bytes:
                raise DocumentTooLargeError("Markdown exceeds configured byte limit")
            raw_bytes = await asyncio.to_thread(self._bytes_provider, resolved)
            after = await asyncio.to_thread(self._stat_provider, resolved)
            if after.st_size > self._max_bytes:
                raise DocumentTooLargeError("Markdown exceeds configured byte limit")
            if _stable(before, after, len(raw_bytes)):
                observation = VaultFileObservation(
                    relative_path=normalized,
                    path_key=path_key(normalized),
                    size=len(raw_bytes),
                    mtime_ns=after.st_mtime_ns,
                    file_key=file_key(after.st_dev, after.st_ino),
                )
                return StableDocument(
                    observation=observation,
                    content_hash=sha256_bytes(raw_bytes),
                    raw_bytes=raw_bytes,
                )
            if attempt + 1 < self._attempts:
                await self._sleep_provider(self._interval_seconds)
        raise UnstableFileError("Markdown changed during stable capture")


def decode_markdown(raw_bytes: bytes) -> str:
    """Decode UTF-8 or UTF-8 BOM without modifying line endings."""

    try:
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UnsupportedEncodingError("Markdown encoding is unsupported") from error


def _stat(path: Path) -> os.stat_result:
    return path.stat(follow_symlinks=False)


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _stable(before: os.stat_result, after: os.stat_result, byte_size: int) -> bool:
    return (
        before.st_size == after.st_size == byte_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
    )


__all__ = ["StableMarkdownReader", "decode_markdown"]
