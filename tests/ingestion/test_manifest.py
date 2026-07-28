from __future__ import annotations

import pytest

from trustworthy_kb.ingestion import (
    IngestionError,
    ManifestEntry,
    build_manifest,
    path_key,
)


def entry(relative_path: str, content_hash: str) -> ManifestEntry:
    return ManifestEntry(
        relative_path=relative_path,
        path_key=path_key(relative_path),
        size=12,
        mtime_ns=100,
        content_hash=content_hash,
    )


def test_manifest_sort_and_hash_are_order_independent() -> None:
    first = entry("B.md", "b" * 64)
    second = entry("A.md", "a" * 64)

    left = build_manifest([first, second], complete=True)
    right = build_manifest([second, first], complete=True)

    assert left == right
    assert [item.path_key for item in left.entries] == sorted(
        item.path_key for item in left.entries
    )


def test_manifest_supports_safe_capture_errors_and_rejects_duplicate_identity() -> None:
    failed = ManifestEntry(
        relative_path="Failed.md",
        path_key=path_key("Failed.md"),
        size=12,
        mtime_ns=100,
        error_category="UNSTABLE_FILE",
    )
    assert build_manifest([failed], complete=True).entries == (failed,)

    with pytest.raises(IngestionError, match="duplicate"):
        build_manifest([failed, failed], complete=True)
