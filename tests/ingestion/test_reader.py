from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trustworthy_kb.ingestion import (
    DocumentTooLargeError,
    StableMarkdownReader,
    UnstableFileError,
    UnsupportedEncodingError,
    decode_markdown,
    sha256_bytes,
)


@pytest.mark.asyncio
async def test_reader_preserves_raw_bytes_and_decodes_utf8_bom(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    raw_bytes = b"\xef\xbb\xbf# Synthetic\r\n"
    (vault / "note.md").write_bytes(raw_bytes)
    reader = StableMarkdownReader(vault, max_bytes=1024, interval_ms=0)

    document = await reader.read("note.md")

    assert document.raw_bytes == raw_bytes
    assert document.content_hash == sha256_bytes(raw_bytes)
    assert document.observation.relative_path == "note.md"
    assert decode_markdown(document.raw_bytes) == "# Synthetic\r\n"


@pytest.mark.asyncio
async def test_reader_rejects_large_and_unstable_files(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_bytes(b"12345")

    with pytest.raises(DocumentTooLargeError):
        await StableMarkdownReader(vault, max_bytes=4, interval_ms=0).read("note.md")

    def mutate_while_reading(path: Path) -> bytes:
        captured = path.read_bytes()
        path.write_bytes(captured + b"x")
        return captured

    reader = StableMarkdownReader(
        vault,
        max_bytes=1024,
        attempts=2,
        interval_ms=0,
        bytes_provider=mutate_while_reading,
        sleep_provider=lambda _: asyncio.sleep(0),
    )
    with pytest.raises(UnstableFileError):
        await reader.read("note.md")


def test_decoder_rejects_non_utf8_without_echoing_content() -> None:
    private_bytes = b"\xff\xfeprivate"

    with pytest.raises(UnsupportedEncodingError) as captured:
        decode_markdown(private_bytes)

    assert "private" not in str(captured.value)
