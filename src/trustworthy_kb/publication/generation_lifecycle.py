"""Full-generation rebuild, gated promotion, abort, and verified rollback."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from trustworthy_kb.answer.contracts import EvaluationMetrics
from trustworthy_kb.domain import (
    ActorType,
    EntityType,
    IndexGenerationId,
    IndexGenerationRecord,
    IndexGenerationStatus,
    IndexJobId,
    IndexJobRecord,
    IndexJobStatus,
    KnowledgeNoteRecord,
    OperationLogId,
    OperationLogRecord,
    operation_log_entry_hash,
)
from trustworthy_kb.domain.base import NonEmptyText, Sha256Hex
from trustworthy_kb.governance.fingerprints import canonical_json_hash
from trustworthy_kb.persistence import SqliteUnitOfWork, SqliteUnitOfWorkFactory
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.publication.chunking import MarkdownChunker
from trustworthy_kb.publication.contracts import KnowledgeChunk, StrictContract
from trustworthy_kb.publication.errors import LifecycleError
from trustworthy_kb.publication.indexing import GenerationIndexer
from trustworthy_kb.publication.ports import VectorIndexGateway
from trustworthy_kb.publication.snapshot_store import PublicationSnapshotStore


class GenerationPromotionGate(StrictContract):
    """Generation-bound deterministic safety metrics required before activation."""

    mode: Literal["deterministic"]
    generation_id: IndexGenerationId
    passed: bool
    metrics: EvaluationMetrics

    @model_validator(mode="after")
    def _thresholds_pass(self) -> GenerationPromotionGate:
        metrics = self.metrics
        if (
            not self.passed
            or metrics.citation_precision < 0.95
            or metrics.retrieval_recall < 0.90
            or metrics.refusal_accuracy != 1.0
            or metrics.unsafe_citation_count != 0
        ):
            raise ValueError("generation promotion metrics did not pass")
        return self

    @classmethod
    def load(cls, path: Path, *, max_bytes: int = 1024 * 1024) -> GenerationPromotionGate:
        """Load one bounded, non-symlink JSON report emitted by the evaluation CLI."""

        try:
            target = path.expanduser()
            if target.is_symlink() or not target.is_file() or target.stat().st_size > max_bytes:
                raise LifecycleError("generation promotion gate is unavailable or unsafe")
            return cls.model_validate_json(target.read_bytes())
        except LifecycleError:
            raise
        except (OSError, ValidationError, ValueError):
            raise LifecycleError("generation promotion gate is invalid") from None


class GenerationLifecycleAction(StrEnum):
    REBUILD = "REBUILD"
    PROMOTE = "PROMOTE"
    ROLLBACK = "ROLLBACK"
    ABORT = "ABORT"


class GenerationLifecycleReport(StrictContract):
    action: GenerationLifecycleAction
    operation_id: NonEmptyText
    source_generation_id: IndexGenerationId
    target_generation_id: IndexGenerationId
    active_generation_id: IndexGenerationId
    target_status: IndexGenerationStatus
    note_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    gate_hash: Sha256Hex | None = None


class GenerationLifecycleService:
    """Build complete immutable generations and switch all live pointers atomically."""

    def __init__(
        self,
        *,
        unit_of_work_factory: SqliteUnitOfWorkFactory,
        snapshots: PublicationSnapshotStore,
        chunker: MarkdownChunker,
        indexer: GenerationIndexer,
        index: VectorIndexGateway,
    ) -> None:
        self._uow_factory = unit_of_work_factory
        self._snapshots = snapshots
        self._chunker = chunker
        self._indexer = indexer
        self._index = index

    async def rebuild(
        self,
        generation_id: IndexGenerationId,
        *,
        operation_id: str,
    ) -> GenerationLifecycleReport:
        """Populate and strongly verify a STAGING generation without changing live pointers."""

        operation_id = _operation_id(operation_id)
        source, target, notes = await self._migration_context(generation_id)
        chunks_by_note = await self._chunks_for_notes(notes, target)
        chunk_count = 0
        for note, chunks in chunks_by_note:
            chunk_count += await self._index_note(note, target, chunks, operation_id=operation_id)
            await self._verify_chunks(target, chunks)
        async with self._uow_factory() as unit_of_work:
            await _append_audit(
                unit_of_work,
                operation_id=operation_id,
                action="INDEX_GENERATION_REBUILT",
                generation_id=target.id,
                before={"active_generation_id": str(source.id)},
                after={
                    "active_generation_id": str(source.id),
                    "chunk_count": chunk_count,
                    "note_count": len(notes),
                    "target_status": target.status.value,
                },
            )
            await unit_of_work.commit()
        return GenerationLifecycleReport(
            action=GenerationLifecycleAction.REBUILD,
            operation_id=operation_id,
            source_generation_id=source.id,
            target_generation_id=target.id,
            active_generation_id=source.id,
            target_status=target.status,
            note_count=len(notes),
            chunk_count=chunk_count,
        )

    async def promote(
        self,
        generation_id: IndexGenerationId,
        *,
        gate: GenerationPromotionGate,
        operation_id: str,
    ) -> GenerationLifecycleReport:
        """Verify a completed rebuild and its generation-bound gate before atomic activation."""

        operation_id = _operation_id(operation_id)
        if gate.generation_id != generation_id:
            raise LifecycleError("generation promotion gate targets a different generation")
        gate_hash = canonical_json_hash(gate.model_dump(mode="json"))
        replay = await self._replay(
            generation_id,
            operation_id=operation_id,
            action=GenerationLifecycleAction.PROMOTE,
            audit_action="INDEX_GENERATION_PROMOTED",
            gate_hash=gate_hash,
        )
        if replay is not None:
            return replay
        source, target, notes = await self._migration_context(generation_id)
        chunks_by_note = await self._chunks_for_notes(notes, target)
        chunk_count = 0
        for note, chunks in chunks_by_note:
            await self._require_indexed_job(note, target)
            await self._verify_chunks(target, chunks)
            chunk_count += len(chunks)

        async with self._uow_factory() as unit_of_work:
            current_target = await unit_of_work.publication.get_index_generation(target.id)
            _, active, note_count = await unit_of_work.publication.promote_index_generation(
                current_target.id,
                expected_revision=current_target.revision,
            )
            await _append_audit(
                unit_of_work,
                operation_id=operation_id,
                action="INDEX_GENERATION_PROMOTED",
                generation_id=target.id,
                before={
                    "active_generation_id": str(source.id),
                    "target_status": target.status.value,
                },
                after={
                    "active_generation_id": str(active.id),
                    "chunk_count": chunk_count,
                    "gate_hash": gate_hash,
                    "note_count": note_count,
                },
            )
            await unit_of_work.commit()
        return GenerationLifecycleReport(
            action=GenerationLifecycleAction.PROMOTE,
            operation_id=operation_id,
            source_generation_id=source.id,
            target_generation_id=target.id,
            active_generation_id=active.id,
            target_status=active.status,
            note_count=note_count,
            chunk_count=chunk_count,
            gate_hash=gate_hash,
        )

    async def rollback(
        self,
        generation_id: IndexGenerationId,
        *,
        operation_id: str,
    ) -> GenerationLifecycleReport:
        """Verify retained vectors before atomically restoring a superseded generation."""

        operation_id = _operation_id(operation_id)
        replay = await self._replay(
            generation_id,
            operation_id=operation_id,
            action=GenerationLifecycleAction.ROLLBACK,
            audit_action="INDEX_GENERATION_ROLLED_BACK",
        )
        if replay is not None:
            return replay
        async with self._uow_factory() as unit_of_work:
            source = await unit_of_work.publication.get_active_index_generation()
            target = await unit_of_work.publication.get_index_generation(generation_id)
            if source is None or target.status is not IndexGenerationStatus.SUPERSEDED:
                raise LifecycleError("generation is not eligible for rollback")
            notes = await unit_of_work.publication.list_active_notes(source.id)
        chunks_by_note = await self._chunks_for_notes(notes, target)
        chunk_count = 0
        for _, chunks in chunks_by_note:
            await self._verify_chunks(target, chunks)
            chunk_count += len(chunks)

        async with self._uow_factory() as unit_of_work:
            current_target = await unit_of_work.publication.get_index_generation(target.id)
            _, active, note_count = await unit_of_work.publication.rollback_index_generation(
                current_target.id,
                expected_revision=current_target.revision,
            )
            await _append_audit(
                unit_of_work,
                operation_id=operation_id,
                action="INDEX_GENERATION_ROLLED_BACK",
                generation_id=target.id,
                before={"active_generation_id": str(source.id)},
                after={
                    "active_generation_id": str(active.id),
                    "chunk_count": chunk_count,
                    "note_count": note_count,
                },
            )
            await unit_of_work.commit()
        return GenerationLifecycleReport(
            action=GenerationLifecycleAction.ROLLBACK,
            operation_id=operation_id,
            source_generation_id=source.id,
            target_generation_id=target.id,
            active_generation_id=active.id,
            target_status=active.status,
            note_count=note_count,
            chunk_count=chunk_count,
        )

    async def abort(
        self,
        generation_id: IndexGenerationId,
        *,
        operation_id: str,
    ) -> GenerationLifecycleReport:
        """Mark an unusable staging generation failed while proving the old one stays active."""

        operation_id = _operation_id(operation_id)
        async with self._uow_factory() as unit_of_work:
            active = await unit_of_work.publication.get_active_index_generation()
            target = await unit_of_work.publication.get_index_generation(generation_id)
            if active is None or target.id == active.id:
                raise LifecycleError("active generation cannot be aborted")
            if target.status is IndexGenerationStatus.STAGING:
                failed = await unit_of_work.publication.transition_index_generation(
                    target.id,
                    IndexGenerationStatus.FAILED,
                    expected_revision=target.revision,
                )
            elif target.status is IndexGenerationStatus.FAILED:
                failed = target
            else:
                raise LifecycleError("generation is not eligible for abort")
            await _append_audit(
                unit_of_work,
                operation_id=operation_id,
                action="INDEX_GENERATION_ABORTED",
                generation_id=target.id,
                before={"target_status": target.status.value},
                after={
                    "active_generation_id": str(active.id),
                    "target_status": failed.status.value,
                },
            )
            await unit_of_work.commit()
        return GenerationLifecycleReport(
            action=GenerationLifecycleAction.ABORT,
            operation_id=operation_id,
            source_generation_id=active.id,
            target_generation_id=target.id,
            active_generation_id=active.id,
            target_status=failed.status,
            note_count=0,
            chunk_count=0,
        )

    async def _migration_context(
        self, generation_id: IndexGenerationId
    ) -> tuple[IndexGenerationRecord, IndexGenerationRecord, tuple[KnowledgeNoteRecord, ...]]:
        async with self._uow_factory() as unit_of_work:
            source = await unit_of_work.publication.get_active_index_generation()
            target = await unit_of_work.publication.get_index_generation(generation_id)
            if (
                source is None
                or source.id == target.id
                or target.status is not IndexGenerationStatus.STAGING
            ):
                raise LifecycleError("generation is not eligible for migration")
            notes = await unit_of_work.publication.list_active_notes(source.id)
        if (
            target.embedding_model != self._indexer.embedding_model
            or target.embedding_dimension != self._indexer.embedding_dimension
            or target.chunker_version != self._chunker.version
        ):
            raise LifecycleError("generation runtime does not match its immutable manifest")
        return source, target, notes

    async def _replay(
        self,
        generation_id: IndexGenerationId,
        *,
        operation_id: str,
        action: GenerationLifecycleAction,
        audit_action: str,
        gate_hash: str | None = None,
    ) -> GenerationLifecycleReport | None:
        async with self._uow_factory() as unit_of_work:
            previous = await unit_of_work.audit.get_latest_operation_log(operation_id)
            target = await unit_of_work.publication.get_index_generation(generation_id)
        if previous is None or previous.action != audit_action:
            return None
        if previous.target_id != generation_id or target.status is not IndexGenerationStatus.ACTIVE:
            raise LifecycleError("completed generation operation no longer matches active state")
        try:
            source_id = IndexGenerationId(str(previous.before_json["active_generation_id"]))
            active_id = IndexGenerationId(str(previous.after_json["active_generation_id"]))
            note_count = int(previous.after_json["note_count"])
            chunk_count = int(previous.after_json["chunk_count"])
            stored_gate_hash = previous.after_json.get("gate_hash")
        except (KeyError, TypeError, ValueError):
            raise LifecycleError("completed generation operation audit is invalid") from None
        if active_id != generation_id or (gate_hash is not None and stored_gate_hash != gate_hash):
            raise LifecycleError("completed generation operation does not match this request")
        return GenerationLifecycleReport(
            action=action,
            operation_id=operation_id,
            source_generation_id=source_id,
            target_generation_id=generation_id,
            active_generation_id=active_id,
            target_status=target.status,
            note_count=note_count,
            chunk_count=chunk_count,
            gate_hash=gate_hash,
        )

    async def _chunks_for_notes(
        self,
        notes: tuple[KnowledgeNoteRecord, ...],
        generation: IndexGenerationRecord,
    ) -> tuple[tuple[KnowledgeNoteRecord, tuple[KnowledgeChunk, ...]], ...]:
        if generation.chunker_version != self._chunker.version:
            raise LifecycleError("generation Chunker implementation is unavailable")
        rows: list[tuple[KnowledgeNoteRecord, tuple[KnowledgeChunk, ...]]] = []
        for note in notes:
            if note.current_curated_version_id is None:
                raise LifecycleError("active note has no curated version")
            async with self._uow_factory() as unit_of_work:
                version = await unit_of_work.publication.get_curated_version(
                    note.current_curated_version_id
                )
            snapshot = await self._snapshots.get(version.content_hash)
            if (
                snapshot.artifact.note_id != note.id
                or snapshot.artifact.curated_version_id != version.id
            ):
                raise LifecycleError("generation rebuild snapshot lineage changed")
            rows.append(
                (
                    note,
                    self._chunker.chunk(
                        snapshot.artifact,
                        snapshot.claims,
                        generation_id=generation.id,
                        generation_number=generation.generation_number,
                        embedding_model=generation.embedding_model,
                    ),
                )
            )
        return tuple(rows)

    async def _index_note(
        self,
        note: KnowledgeNoteRecord,
        generation: IndexGenerationRecord,
        chunks: tuple[KnowledgeChunk, ...],
        *,
        operation_id: str,
    ) -> int:
        if note.current_curated_version_id is None:
            raise LifecycleError("active note has no curated version")
        async with self._uow_factory() as unit_of_work:
            job = await unit_of_work.publication.find_version_index_job(
                note.current_curated_version_id,
                generation.id,
            )
            if job is None:
                timestamp = utc_now()
                job = await unit_of_work.publication.add_index_job(
                    IndexJobRecord(
                        id=IndexJobId.generate(),
                        object_type=EntityType.CURATED_VERSION,
                        object_id=note.current_curated_version_id,
                        generation_id=generation.id,
                        status=IndexJobStatus.PENDING,
                        attempt=0,
                        operation_id=f"{operation_id}:{note.id}",
                        revision=1,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
            if job.status is IndexJobStatus.FAILED:
                job = await unit_of_work.publication.transition_index_job(
                    job.id,
                    IndexJobStatus.PENDING,
                    expected_revision=job.revision,
                )
            if job.status is IndexJobStatus.PENDING:
                job = await unit_of_work.publication.transition_index_job(
                    job.id,
                    IndexJobStatus.INDEXING,
                    expected_revision=job.revision,
                )
            await unit_of_work.commit()

        if job.status is IndexJobStatus.INDEXING:
            try:
                count = await self._indexer.index(chunks)
            except Exception as error:
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
                raise
            async with self._uow_factory() as unit_of_work:
                current = await unit_of_work.publication.get_index_job(job.id)
                job = await unit_of_work.publication.mark_index_job_indexed(
                    current.id,
                    content_hash=chunks[0].content_hash,
                    indexed_chunk_count=count,
                    expected_revision=current.revision,
                )
                await unit_of_work.commit()
        if job.status is not IndexJobStatus.INDEXED:
            raise LifecycleError("generation rebuild index job is not verified")
        return len(chunks)

    async def _require_indexed_job(
        self,
        note: KnowledgeNoteRecord,
        generation: IndexGenerationRecord,
    ) -> None:
        if note.current_curated_version_id is None:
            raise LifecycleError("active note has no curated version")
        async with self._uow_factory() as unit_of_work:
            job = await unit_of_work.publication.find_version_index_job(
                note.current_curated_version_id,
                generation.id,
            )
        if job is None or job.status is not IndexJobStatus.INDEXED:
            raise LifecycleError("generation must be completely rebuilt before promotion")

    async def _verify_chunks(
        self,
        generation: IndexGenerationRecord,
        chunks: tuple[KnowledgeChunk, ...],
    ) -> None:
        probes = await self._index.fetch_probes(
            generation.generation_number,
            [chunk.chunk_id for chunk in chunks],
        )
        expected = {
            (chunk.chunk_id, chunk.curated_version_id, chunk.content_hash) for chunk in chunks
        }
        actual = {
            (probe.chunk_id, probe.curated_version_id, probe.content_hash) for probe in probes
        }
        if actual != expected or len(probes) != len(chunks):
            raise LifecycleError("generation vector verification did not converge")


async def _append_audit(
    unit_of_work: SqliteUnitOfWork,
    *,
    operation_id: str,
    action: str,
    generation_id: IndexGenerationId,
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    previous = await unit_of_work.audit.get_latest_operation_log(operation_id)
    if previous is not None and previous.target_id != generation_id:
        raise LifecycleError("lifecycle operation ID belongs to a different generation")
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
        target_type=EntityType.INDEX_GENERATION,
        target_id=generation_id,
        before_json=before,
        after_json=after,
        previous_entry_hash=previous_hash,
        created_at=timestamp,
    )
    await unit_of_work.audit.append_operation_log(
        OperationLogRecord(
            id=OperationLogId.generate(),
            operation_id=operation_id,
            step_number=step,
            actor_type=ActorType.SYSTEM,
            action=action,
            target_type=EntityType.INDEX_GENERATION,
            target_id=generation_id,
            before_json=before,
            after_json=after,
            previous_entry_hash=previous_hash,
            entry_hash=entry_hash,
            created_at=timestamp,
        )
    )


def _operation_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 180:
        raise LifecycleError("generation lifecycle operation ID is invalid")
    return normalized


__all__ = [
    "GenerationLifecycleAction",
    "GenerationLifecycleReport",
    "GenerationLifecycleService",
    "GenerationPromotionGate",
]
