"""Pure full-scan change planner."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence

from pydantic import Field

from trustworthy_kb.domain import (
    IngestionAction,
    SourceId,
    SourceVersionId,
    SourceVersionStatus,
)
from trustworthy_kb.ingestion.errors import IngestionError
from trustworthy_kb.ingestion.manifest import IngestionManifest, ManifestEntry
from trustworthy_kb.ingestion.types import IngestionValue


class KnownSource(IngestionValue):
    source_id: SourceId
    relative_path: str = Field(min_length=1)
    path_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    current_version_id: SourceVersionId | None = None
    current_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    latest_version_id: SourceVersionId | None = None
    latest_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    latest_status: SourceVersionStatus | None = None
    eligible_for_deletion: bool = True


class IngestionPlanItem(IngestionValue):
    action: IngestionAction
    relative_path: str = Field(min_length=1)
    path_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: SourceId | None = None
    file_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    base_version_id: SourceVersionId | None = None
    error_category: str | None = None
    preserve_quarantine: bool = False


class IngestionPlan(IngestionValue):
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: tuple[IngestionPlanItem, ...]


def plan_ingestion(
    manifest: IngestionManifest,
    known_sources: Sequence[KnownSource],
) -> IngestionPlan:
    """Plan changes without guessing ambiguous file identity."""

    if not manifest.complete:
        raise IngestionError("inventory is incomplete; deletion planning is disabled")
    known_by_path = _unique_known_sources(known_sources)
    entries_by_path = {entry.path_key: entry for entry in manifest.entries}
    matched_known: set[SourceId] = set()
    matched_entries: set[str] = set()
    items: list[IngestionPlanItem] = []

    for path_identity in sorted(known_by_path.keys() & entries_by_path.keys()):
        known = known_by_path[path_identity]
        entry = entries_by_path[path_identity]
        matched_known.add(known.source_id)
        matched_entries.add(entry.path_key)
        items.append(_same_path_item(known, entry))

    unmatched_known = [source for source in known_sources if source.source_id not in matched_known]
    unmatched_entries = [
        entry for entry in manifest.entries if entry.path_key not in matched_entries
    ]
    _match_unique_identity(
        unmatched_known,
        unmatched_entries,
        matched_known,
        matched_entries,
        items,
        identity=lambda source: source.file_key,
        entry_identity=lambda entry: entry.file_key,
    )
    _match_unique_identity(
        unmatched_known,
        unmatched_entries,
        matched_known,
        matched_entries,
        items,
        identity=lambda source: source.latest_content_hash,
        entry_identity=lambda entry: entry.content_hash,
    )

    for entry in manifest.entries:
        if entry.path_key not in matched_entries:
            items.append(
                IngestionPlanItem(
                    action=IngestionAction.CREATED,
                    relative_path=entry.relative_path,
                    path_key=entry.path_key,
                    file_key=entry.file_key,
                    content_hash=entry.content_hash,
                    error_category=entry.error_category,
                )
            )
    for known in known_sources:
        if known.source_id not in matched_known and known.eligible_for_deletion:
            items.append(
                IngestionPlanItem(
                    action=IngestionAction.DELETED,
                    relative_path=known.relative_path,
                    path_key=known.path_key,
                    source_id=known.source_id,
                    file_key=known.file_key,
                    base_version_id=known.current_version_id or known.latest_version_id,
                )
            )
    return IngestionPlan(
        manifest_hash=manifest.manifest_hash,
        items=tuple(sorted(items, key=lambda item: (item.path_key, item.action.value))),
    )


def _unique_known_sources(known_sources: Sequence[KnownSource]) -> dict[str, KnownSource]:
    counts = Counter(source.path_key for source in known_sources)
    if any(count != 1 for count in counts.values()):
        raise IngestionError("known sources contain duplicate live path identities")
    return {source.path_key: source for source in known_sources}


def _same_path_item(known: KnownSource, entry: ManifestEntry) -> IngestionPlanItem:
    comparison_hash = known.latest_content_hash or known.current_content_hash
    if entry.error_category is not None:
        action = IngestionAction.UPDATED
    elif entry.content_hash != comparison_hash:
        action = IngestionAction.UPDATED
    elif known.latest_status is SourceVersionStatus.PARSE_FAILED:
        action = IngestionAction.UPDATED
    else:
        action = IngestionAction.UNCHANGED
    return IngestionPlanItem(
        action=action,
        relative_path=entry.relative_path,
        path_key=entry.path_key,
        source_id=known.source_id,
        file_key=entry.file_key,
        content_hash=entry.content_hash,
        base_version_id=known.current_version_id,
        error_category=entry.error_category,
        preserve_quarantine=(
            action is IngestionAction.UNCHANGED
            and known.latest_status is SourceVersionStatus.QUARANTINED
        ),
    )


def _match_unique_identity(
    known_sources: Sequence[KnownSource],
    entries: Sequence[ManifestEntry],
    matched_known: set[SourceId],
    matched_entries: set[str],
    items: list[IngestionPlanItem],
    *,
    identity: Callable[[KnownSource], str | None],
    entry_identity: Callable[[ManifestEntry], str | None],
) -> None:
    available_known = [source for source in known_sources if source.source_id not in matched_known]
    available_entries = [entry for entry in entries if entry.path_key not in matched_entries]
    old_counts = Counter(identity(source) for source in available_known)
    new_counts = Counter(entry_identity(entry) for entry in available_entries)
    unique_values = {
        value
        for value, count in old_counts.items()
        if value is not None and count == 1 and new_counts[value] == 1
    }
    old_by_identity = {identity(source): source for source in available_known}
    new_by_identity = {entry_identity(entry): entry for entry in available_entries}
    for value in sorted(unique_values):
        source = old_by_identity[value]
        entry = new_by_identity[value]
        matched_known.add(source.source_id)
        matched_entries.add(entry.path_key)
        items.append(
            IngestionPlanItem(
                action=IngestionAction.MOVED,
                relative_path=entry.relative_path,
                path_key=entry.path_key,
                source_id=source.source_id,
                file_key=entry.file_key,
                content_hash=entry.content_hash,
                base_version_id=source.current_version_id,
                error_category=entry.error_category,
            )
        )


__all__ = ["IngestionPlan", "IngestionPlanItem", "KnownSource", "plan_ingestion"]
