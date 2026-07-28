"""Recoverable note deletion and restoration with fail-closed ordering."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field

from trustworthy_kb.domain import (
    ActorType,
    CuratedVersionRecord,
    EntityType,
    IndexGenerationRecord,
    IndexJobRecord,
    IndexJobStatus,
    KnowledgeNoteId,
    KnowledgeNoteRecord,
    OperationLogId,
    OperationLogRecord,
    operation_log_entry_hash,
)
from trustworthy_kb.domain.base import NonEmptyText
from trustworthy_kb.persistence import SqliteUnitOfWork, SqliteUnitOfWorkFactory
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.publication.chunking import MarkdownChunker
from trustworthy_kb.publication.contracts import KnowledgeChunk, StrictContract
from trustworthy_kb.publication.errors import LifecycleError
from trustworthy_kb.publication.indexing import GenerationIndexer
from trustworthy_kb.publication.ports import AnswerInvalidationGateway, LifecycleVaultGateway
from trustworthy_kb.publication.snapshot_store import PublicationSnapshotStore


class NoteLifecycleAction(StrEnum):
    DELETE = "DELETE"
    RESTORE = "RESTORE"


class NoteLifecycleReport(StrictContract):
    action: NoteLifecycleAction
    operation_id: NonEmptyText
    note_id: KnowledgeNoteId
    deleted: bool
    index_status: IndexJobStatus
    chunk_count: int = Field(ge=0)
    invalidated_answer_count: int = Field(ge=0)
    vault_path: NonEmptyText


@dataclass(frozen=True, slots=True)
class _LifecycleContext:
    note: KnowledgeNoteRecord
    version: CuratedVersionRecord
    generation: IndexGenerationRecord
    job: IndexJobRecord
    chunks: tuple[KnowledgeChunk, ...]


class NoteLifecycleService:
    """Coordinate SQLite, Vault, vector, and answer invalidation as a resumable Saga."""

    def __init__(
        self,
        *,
        unit_of_work_factory: SqliteUnitOfWorkFactory,
        snapshots: PublicationSnapshotStore,
        chunker: MarkdownChunker,
        indexer: GenerationIndexer,
        vault: LifecycleVaultGateway,
        answers: AnswerInvalidationGateway | None = None,
    ) -> None:
        self._uow_factory = unit_of_work_factory
        self._snapshots = snapshots
        self._chunker = chunker
        self._indexer = indexer
        self._vault = vault
        self._answers = answers

    async def delete(self, note_id: KnowledgeNoteId, *, operation_id: str) -> NoteLifecycleReport:
        """Exclude, recycle, and erase one note's vectors with idempotent recovery."""

        operation_id = _operation_id(operation_id)
        context = await self._load(note_id)
        if context.note.deleted_at is None:
            if context.job.status is not IndexJobStatus.ACTIVE_INDEXED:
                raise LifecycleError("active note index is not eligible for deletion")
            async with self._uow_factory() as unit_of_work:
                note = await unit_of_work.publication.get_note(note_id)
                job = await unit_of_work.publication.get_index_job(context.job.id)
                deleted_note, pending_job = await unit_of_work.publication.prepare_note_deletion(
                    note.id,
                    job.id,
                    expected_note_revision=note.revision,
                    expected_job_revision=job.revision,
                )
                await self._append_audit(
                    unit_of_work,
                    operation_id=operation_id,
                    action="KNOWLEDGE_NOTE_DELETE_PREPARED",
                    note_id=note.id,
                    before={"deleted": False, "index_status": job.status.value},
                    after={
                        "deleted": True,
                        "index_status": pending_job.status.value,
                        "revision": deleted_note.revision,
                    },
                )
                await unit_of_work.commit()
            context = await self._load(note_id)

        if context.job.status not in {IndexJobStatus.DELETE_PENDING, IndexJobStatus.DELETED}:
            raise LifecycleError("deleted note has an invalid index lifecycle state")

        await self._vault.recycle(
            context.version.vault_path,
            note_id=context.note.id,
            version_id=context.version.id,
            expected_hash=context.version.content_hash,
        )
        deleted_chunks = await self._indexer.delete(context.chunks)
        invalidated = await self._invalidate_answers(context.chunks)

        if context.job.status is IndexJobStatus.DELETE_PENDING:
            async with self._uow_factory() as unit_of_work:
                job = await unit_of_work.publication.get_index_job(context.job.id)
                deleted_job = await unit_of_work.publication.transition_index_job(
                    job.id,
                    IndexJobStatus.DELETED,
                    expected_revision=job.revision,
                )
                await self._append_audit(
                    unit_of_work,
                    operation_id=operation_id,
                    action="KNOWLEDGE_NOTE_DELETED",
                    note_id=context.note.id,
                    before={"index_status": job.status.value},
                    after={
                        "deleted": True,
                        "index_status": deleted_job.status.value,
                        "invalidated_answers": invalidated,
                    },
                )
                await unit_of_work.commit()
            context = await self._load(note_id)

        return NoteLifecycleReport(
            action=NoteLifecycleAction.DELETE,
            operation_id=operation_id,
            note_id=context.note.id,
            deleted=True,
            index_status=context.job.status,
            chunk_count=deleted_chunks,
            invalidated_answer_count=invalidated,
            vault_path=context.version.vault_path,
        )

    async def restore(
        self,
        note_id: KnowledgeNoteId,
        *,
        operation_id: str,
    ) -> NoteLifecycleReport:
        """Rebuild and verify external state before atomically reopening a recycled note."""

        operation_id = _operation_id(operation_id)
        context = await self._load(note_id)
        if context.note.deleted_at is None:
            if context.job.status is not IndexJobStatus.ACTIVE_INDEXED:
                raise LifecycleError("live note index is not active")
            await self._vault.verify(
                context.version.vault_path,
                expected_hash=context.version.content_hash,
            )
            count = await self._indexer.index(context.chunks)
            return self._restore_report(context, operation_id=operation_id, chunk_count=count)

        if context.job.status is IndexJobStatus.DELETE_PENDING:
            raise LifecycleError("note deletion must finish before restoration")
        if context.job.status in {IndexJobStatus.DELETED, IndexJobStatus.FAILED}:
            async with self._uow_factory() as unit_of_work:
                job = await unit_of_work.publication.get_index_job(context.job.id)
                pending = await unit_of_work.publication.transition_index_job(
                    job.id,
                    IndexJobStatus.PENDING,
                    expected_revision=job.revision,
                )
                indexing = await unit_of_work.publication.transition_index_job(
                    pending.id,
                    IndexJobStatus.INDEXING,
                    expected_revision=pending.revision,
                )
                await self._append_audit(
                    unit_of_work,
                    operation_id=operation_id,
                    action="KNOWLEDGE_NOTE_RESTORE_PREPARED",
                    note_id=context.note.id,
                    before={"deleted": True, "index_status": job.status.value},
                    after={"deleted": True, "index_status": indexing.status.value},
                )
                await unit_of_work.commit()
            context = await self._load(note_id)
        elif context.job.status is IndexJobStatus.PENDING:
            async with self._uow_factory() as unit_of_work:
                job = await unit_of_work.publication.get_index_job(context.job.id)
                await unit_of_work.publication.transition_index_job(
                    job.id,
                    IndexJobStatus.INDEXING,
                    expected_revision=job.revision,
                )
                await unit_of_work.commit()
            context = await self._load(note_id)

        if context.job.status is IndexJobStatus.INDEXING:
            try:
                count = await self._indexer.index(context.chunks)
            except Exception as error:
                await self._fail_restore_index(context.job, error)
                raise
            async with self._uow_factory() as unit_of_work:
                job = await unit_of_work.publication.get_index_job(context.job.id)
                await unit_of_work.publication.mark_index_job_indexed(
                    job.id,
                    content_hash=context.version.content_hash,
                    indexed_chunk_count=count,
                    expected_revision=job.revision,
                )
                await unit_of_work.commit()
            context = await self._load(note_id)
        elif context.job.status is IndexJobStatus.INDEXED:
            count = context.job.indexed_chunk_count
        else:
            raise LifecycleError("deleted note is not ready for restoration")

        await self._vault.restore_recycled(
            context.version.vault_path,
            note_id=context.note.id,
            version_id=context.version.id,
            expected_hash=context.version.content_hash,
        )
        await self._vault.verify(
            context.version.vault_path,
            expected_hash=context.version.content_hash,
        )
        async with self._uow_factory() as unit_of_work:
            note = await unit_of_work.publication.get_note(note_id, include_deleted=True)
            job = await unit_of_work.publication.get_index_job(context.job.id)
            restored_note, active_job = await unit_of_work.publication.restore_note(
                note.id,
                job.id,
                expected_note_revision=note.revision,
                expected_job_revision=job.revision,
            )
            await self._append_audit(
                unit_of_work,
                operation_id=operation_id,
                action="KNOWLEDGE_NOTE_RESTORED",
                note_id=note.id,
                before={"deleted": True, "index_status": job.status.value},
                after={
                    "deleted": False,
                    "index_status": active_job.status.value,
                    "revision": restored_note.revision,
                },
            )
            await unit_of_work.commit()
        return self._restore_report(
            await self._load(note_id),
            operation_id=operation_id,
            chunk_count=count,
        )

    async def _load(self, note_id: KnowledgeNoteId) -> _LifecycleContext:
        async with self._uow_factory() as unit_of_work:
            note = await unit_of_work.publication.get_note(note_id, include_deleted=True)
            if note.current_curated_version_id is None or note.active_index_generation_id is None:
                raise LifecycleError("knowledge note has no active publication to manage")
            version = await unit_of_work.publication.get_curated_version(
                note.current_curated_version_id
            )
            generation = await unit_of_work.publication.get_index_generation(
                note.active_index_generation_id
            )
            job = await unit_of_work.publication.find_version_index_job(
                version.id,
                generation.id,
            )
        if job is None:
            raise LifecycleError("knowledge note index job is unavailable")
        if generation.chunker_version != self._chunker.version:
            raise LifecycleError("knowledge note Chunker version is unavailable")
        snapshot = await self._snapshots.get(version.content_hash)
        if (
            snapshot.artifact.note_id != note.id
            or snapshot.artifact.curated_version_id != version.id
        ):
            raise LifecycleError("knowledge note snapshot lineage changed")
        chunks = self._chunker.chunk(
            snapshot.artifact,
            snapshot.claims,
            generation_id=generation.id,
            generation_number=generation.generation_number,
            embedding_model=generation.embedding_model,
        )
        return _LifecycleContext(note, version, generation, job, chunks)

    async def _invalidate_answers(self, chunks: tuple[KnowledgeChunk, ...]) -> int:
        if self._answers is None:
            return 0
        return await self._answers.purge_by_chunk_ids(frozenset(chunk.chunk_id for chunk in chunks))

    async def _fail_restore_index(self, job: IndexJobRecord, error: Exception) -> None:
        async with self._uow_factory() as unit_of_work:
            current = await unit_of_work.publication.get_index_job(job.id)
            if current.status is IndexJobStatus.INDEXING:
                await unit_of_work.publication.transition_index_job(
                    current.id,
                    IndexJobStatus.FAILED,
                    expected_revision=current.revision,
                    error_category=type(error).__name__.upper()[:100],
                )
                await unit_of_work.commit()

    async def _append_audit(
        self,
        unit_of_work: SqliteUnitOfWork,
        *,
        operation_id: str,
        action: str,
        note_id: KnowledgeNoteId,
        before: dict[str, object],
        after: dict[str, object],
    ) -> None:
        audit = unit_of_work.audit
        previous = await audit.get_latest_operation_log(operation_id)
        if previous is not None and previous.target_id != note_id:
            raise LifecycleError("lifecycle operation ID belongs to a different note")
        if previous is not None and previous.action == action:
            return
        timestamp = utc_now()
        step = 0 if previous is None else previous.step_number + 1
        previous_hash = None if previous is None else previous.entry_hash
        entry_hash = operation_log_entry_hash(
            operation_id=operation_id,
            step_number=step,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            action=action,
            target_type=EntityType.KNOWLEDGE_NOTE,
            target_id=note_id,
            before_json=before,
            after_json=after,
            previous_entry_hash=previous_hash,
            created_at=timestamp,
        )
        await audit.append_operation_log(
            OperationLogRecord(
                id=OperationLogId.generate(),
                operation_id=operation_id,
                step_number=step,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                action=action,
                target_type=EntityType.KNOWLEDGE_NOTE,
                target_id=note_id,
                before_json=before,
                after_json=after,
                previous_entry_hash=previous_hash,
                entry_hash=entry_hash,
                created_at=timestamp,
            )
        )

    @staticmethod
    def _restore_report(
        context: _LifecycleContext,
        *,
        operation_id: str,
        chunk_count: int,
    ) -> NoteLifecycleReport:
        return NoteLifecycleReport(
            action=NoteLifecycleAction.RESTORE,
            operation_id=operation_id,
            note_id=context.note.id,
            deleted=False,
            index_status=context.job.status,
            chunk_count=chunk_count,
            invalidated_answer_count=0,
            vault_path=context.version.vault_path,
        )


def _operation_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise LifecycleError("lifecycle operation ID is invalid")
    return normalized


__all__ = ["NoteLifecycleAction", "NoteLifecycleReport", "NoteLifecycleService"]
