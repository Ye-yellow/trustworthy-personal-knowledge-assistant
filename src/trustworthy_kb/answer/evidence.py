"""Resolve L4 retrieval hits into immutable, locatable answer evidence."""

from __future__ import annotations

from trustworthy_kb.answer.contracts import AnswerEvidence
from trustworthy_kb.domain import CuratedVersionStatus
from trustworthy_kb.persistence import SqliteUnitOfWorkFactory
from trustworthy_kb.publication.contracts import RetrievalResult
from trustworthy_kb.publication.snapshot_store import PublicationSnapshotStore


class SqliteAnswerEvidenceResolver:
    """Reconcile retrieved chunks with authoritative SQLite and publication snapshots."""

    def __init__(
        self,
        unit_of_work_factory: SqliteUnitOfWorkFactory,
        snapshots: PublicationSnapshotStore,
    ) -> None:
        self._uow_factory = unit_of_work_factory
        self._snapshots = snapshots

    async def resolve(self, result: RetrievalResult) -> tuple[AnswerEvidence, ...]:
        async with self._uow_factory() as unit_of_work:
            notes = {
                item.id: item
                for item in await unit_of_work.publication.list_active_notes(result.generation_id)
            }
            rows = []
            for hit in result.hits:
                note = notes.get(hit.chunk.note_id)
                if (
                    note is None
                    or note.current_curated_version_id != hit.chunk.curated_version_id
                    or note.active_index_generation_id != result.generation_id
                ):
                    continue
                version = await unit_of_work.publication.get_curated_version(
                    hit.chunk.curated_version_id
                )
                rows.append((hit, note, version))

        resolved = []
        for hit, note, version in rows:
            if version.status is not CuratedVersionStatus.ACTIVE:
                continue
            try:
                snapshot = await self._snapshots.get(version.content_hash)
            except Exception:
                continue
            artifact = snapshot.artifact
            if (
                artifact.note_id != note.id
                or artifact.curated_version_id != version.id
                or not set(hit.chunk.claim_ids).issubset(artifact.claim_ids)
            ):
                continue
            resolved.append(
                AnswerEvidence(
                    chunk_id=hit.chunk.chunk_id,
                    text=hit.chunk.text,
                    claim_ids=hit.chunk.claim_ids,
                    quality_status=hit.chunk.quality_status,
                    sensitivity=hit.chunk.sensitivity,
                    note_id=note.id,
                    curated_version_id=version.id,
                    generation_id=result.generation_id,
                    vault_path=note.canonical_path,
                    heading_path=hit.chunk.heading_path,
                    source_version_ids=artifact.source_version_ids,
                )
            )
        return tuple(resolved)


__all__ = ["SqliteAnswerEvidenceResolver"]
