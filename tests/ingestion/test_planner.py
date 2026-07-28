from __future__ import annotations

import pytest

from trustworthy_kb.domain import IngestionAction, SourceId, SourceVersionId, SourceVersionStatus
from trustworthy_kb.ingestion import (
    IngestionError,
    KnownSource,
    ManifestEntry,
    build_manifest,
    path_key,
    plan_ingestion,
)


def manifest_entry(
    relative_path: str,
    content_hash: str | None,
    *,
    file_key: str | None = None,
    error_category: str | None = None,
) -> ManifestEntry:
    return ManifestEntry(
        relative_path=relative_path,
        path_key=path_key(relative_path),
        size=12,
        mtime_ns=100,
        file_key=file_key,
        content_hash=content_hash,
        error_category=error_category,
    )


def known_source(
    relative_path: str,
    content_hash: str,
    *,
    file_key: str | None = None,
    status: SourceVersionStatus = SourceVersionStatus.READY,
    eligible_for_deletion: bool = True,
) -> KnownSource:
    return KnownSource(
        source_id=SourceId.generate(),
        relative_path=relative_path,
        path_key=path_key(relative_path),
        file_key=file_key,
        current_version_id=SourceVersionId.generate(),
        current_content_hash=content_hash,
        latest_version_id=SourceVersionId.generate(),
        latest_content_hash=content_hash,
        latest_status=status,
        eligible_for_deletion=eligible_for_deletion,
    )


def test_planner_emits_all_five_actions_deterministically() -> None:
    unchanged = known_source("Unchanged.md", "1" * 64)
    updated = known_source("Updated.md", "2" * 64)
    moved = known_source("Old.md", "3" * 64, file_key="a" * 64)
    deleted = known_source("Deleted.md", "4" * 64)
    entries = [
        manifest_entry("Unchanged.md", "1" * 64),
        manifest_entry("Updated.md", "5" * 64),
        manifest_entry("New.md", "6" * 64),
        manifest_entry("Moved.md", "3" * 64, file_key="a" * 64),
    ]

    plan = plan_ingestion(
        build_manifest(entries, complete=True), [unchanged, updated, moved, deleted]
    )

    assert {item.action for item in plan.items} == set(IngestionAction)
    assert tuple(plan.items) == tuple(
        sorted(plan.items, key=lambda item: (item.path_key, item.action.value))
    )


def test_planner_does_not_guess_ambiguous_move_or_delete_out_of_scope() -> None:
    first = known_source("Old-A.md", "7" * 64)
    second = known_source("Old-B.md", "7" * 64, eligible_for_deletion=False)
    entries = [
        manifest_entry("New-A.md", "7" * 64),
        manifest_entry("New-B.md", "7" * 64),
    ]

    plan = plan_ingestion(build_manifest(entries, complete=True), [first, second])

    assert not any(item.action is IngestionAction.MOVED for item in plan.items)
    assert sum(item.action is IngestionAction.CREATED for item in plan.items) == 2
    assert sum(item.action is IngestionAction.DELETED for item in plan.items) == 1


def test_planner_preserves_failed_inventory_entry_and_quarantine() -> None:
    failed_source = known_source("Failed.md", "8" * 64)
    quarantined = known_source("Quarantined.md", "9" * 64, status=SourceVersionStatus.QUARANTINED)
    entries = [
        manifest_entry("Failed.md", None, error_category="UNSTABLE_FILE"),
        manifest_entry("Quarantined.md", "9" * 64),
    ]

    plan = plan_ingestion(build_manifest(entries, complete=True), [failed_source, quarantined])

    failed_item = next(item for item in plan.items if item.relative_path == "Failed.md")
    quarantined_item = next(item for item in plan.items if item.relative_path == "Quarantined.md")
    assert failed_item.action is IngestionAction.UPDATED
    assert failed_item.error_category == "UNSTABLE_FILE"
    assert quarantined_item.action is IngestionAction.UNCHANGED
    assert quarantined_item.preserve_quarantine
    assert not any(item.action is IngestionAction.DELETED for item in plan.items)


def test_planner_rejects_incomplete_inventory() -> None:
    manifest = build_manifest([manifest_entry("Note.md", "a" * 64)], complete=False)

    with pytest.raises(IngestionError, match="incomplete"):
        plan_ingestion(manifest, [])
