from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trustworthy_kb.ingestion import (
    ContentAddressedSnapshotStore,
    SnapshotIntegrityError,
    sha256_bytes,
)


@pytest.mark.asyncio
async def test_snapshot_store_writes_reuses_and_handles_concurrent_puts(tmp_path: Path) -> None:
    store = ContentAddressedSnapshotStore(tmp_path / "snapshots")
    raw_bytes = b"# Synthetic\r\n"
    content_hash = sha256_bytes(raw_bytes)

    first, second = await asyncio.gather(
        store.put(raw_bytes, content_hash),
        store.put(raw_bytes, content_hash),
    )

    target = tmp_path / "snapshots" / "sha256" / content_hash[:2] / f"{content_hash[2:]}.md"
    assert first == second
    assert target.read_bytes() == raw_bytes
    assert not list(target.parent.glob("*.part"))


@pytest.mark.asyncio
async def test_snapshot_store_rejects_input_and_existing_hash_mismatch(tmp_path: Path) -> None:
    store = ContentAddressedSnapshotStore(tmp_path / "snapshots")
    raw_bytes = b"synthetic"
    content_hash = sha256_bytes(raw_bytes)

    with pytest.raises(SnapshotIntegrityError):
        await store.put(raw_bytes, "0" * 64)

    target = tmp_path / "snapshots" / "sha256" / content_hash[:2] / f"{content_hash[2:]}.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupted")
    with pytest.raises(SnapshotIntegrityError):
        await store.put(raw_bytes, content_hash)
