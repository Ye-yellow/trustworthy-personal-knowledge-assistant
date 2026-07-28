"""Async repository for curation, lineage, and index-control state."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.base import Executable
from sqlalchemy.sql.dml import Update

from trustworthy_kb.domain import (
    CuratedVersionId,
    CuratedVersionRecord,
    CuratedVersionStatus,
    EntityType,
    IndexGenerationId,
    IndexGenerationRecord,
    IndexGenerationStatus,
    IndexJobId,
    IndexJobRecord,
    IndexJobStatus,
    KnowledgeChangeId,
    KnowledgeChangeRecord,
    KnowledgeChangeStatus,
    KnowledgeNoteId,
    KnowledgeNoteRecord,
    LineageEdgeRecord,
    PublicationRunId,
    PublicationRunRecord,
    PublicationRunStatus,
    TypedId,
    require_transition,
)
from trustworthy_kb.persistence.base import ENTITY_TABLE_NAMES, Base, utc_now
from trustworthy_kb.persistence.publication_tables import (
    CuratedVersionTable,
    IndexGenerationTable,
    IndexJobTable,
    KnowledgeChangeTable,
    KnowledgeNoteTable,
    LineageEdgeTable,
    PublicationRunTable,
)
from trustworthy_kb.persistence.repository_base import (
    concurrent,
    flush_safely,
    invariant,
    not_found,
    raise_constraint_error,
    raise_operational_error,
    to_record,
)
from trustworthy_kb.persistence.source_tables import SourceVersionTable


class PublicationRepository:
    """Persist publication-control records and validate cross-table ownership."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_knowledge_change(
        self,
        record: KnowledgeChangeRecord,
    ) -> KnowledgeChangeRecord:
        if record.revision != 1 or record.status is not KnowledgeChangeStatus.RECEIVED:
            raise invariant("knowledge change creation", record.id)
        target = await self._session.get(SourceVersionTable, record.target_version_id)
        if target is None or target.source_id != record.source_id:
            raise invariant("knowledge change target", record.id)
        if record.base_version_id is not None:
            base = await self._session.get(SourceVersionTable, record.base_version_id)
            if base is None or base.source_id != record.source_id:
                raise invariant("knowledge change base", record.id)
        row = KnowledgeChangeTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="knowledge change", identifier=record.id)
        return to_record(KnowledgeChangeRecord, row)

    async def get_knowledge_change(self, change_id: KnowledgeChangeId) -> KnowledgeChangeRecord:
        row = await self._required_row(KnowledgeChangeTable, change_id, "knowledge change")
        return to_record(KnowledgeChangeRecord, row)

    async def list_knowledge_changes(
        self, status: KnowledgeChangeStatus
    ) -> Sequence[KnowledgeChangeRecord]:
        rows = await self._session.scalars(
            select(KnowledgeChangeTable)
            .where(KnowledgeChangeTable.status == status)
            .order_by(KnowledgeChangeTable.created_at, KnowledgeChangeTable.id)
        )
        return tuple(to_record(KnowledgeChangeRecord, row) for row in rows)

    async def transition_knowledge_change(
        self,
        change_id: KnowledgeChangeId,
        target_status: KnowledgeChangeStatus,
        *,
        expected_revision: int,
    ) -> KnowledgeChangeRecord:
        row = await self._required_row(KnowledgeChangeTable, change_id, "knowledge change")
        if row.revision != expected_revision:
            raise concurrent("knowledge change", change_id)
        require_transition(row.status, target_status)
        updated = await self._cas(
            update(KnowledgeChangeTable)
            .where(
                KnowledgeChangeTable.id == change_id,
                KnowledgeChangeTable.revision == expected_revision,
            )
            .values(
                status=target_status,
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(KnowledgeChangeTable),
            select(KnowledgeChangeTable.id).where(KnowledgeChangeTable.id == change_id),
            entity="knowledge change",
            identifier=change_id,
        )
        return to_record(KnowledgeChangeRecord, updated)

    async def add_note(self, record: KnowledgeNoteRecord) -> KnowledgeNoteRecord:
        if (
            record.revision != 1
            or record.current_curated_version_id is not None
            or record.active_index_generation_id is not None
            or record.deleted_at is not None
        ):
            raise invariant("knowledge note creation", record.id)
        row = KnowledgeNoteTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="knowledge note", identifier=record.id)
        return to_record(KnowledgeNoteRecord, row)

    async def get_note(self, note_id: KnowledgeNoteId) -> KnowledgeNoteRecord:
        return to_record(KnowledgeNoteRecord, await self._note_row(note_id))

    async def find_note_by_path(self, canonical_path: str) -> KnowledgeNoteRecord | None:
        row = await self._session.scalar(
            select(KnowledgeNoteTable).where(
                KnowledgeNoteTable.canonical_path == canonical_path,
                KnowledgeNoteTable.deleted_at.is_(None),
            )
        )
        return None if row is None else to_record(KnowledgeNoteRecord, row)

    async def list_active_notes(
        self, generation_id: IndexGenerationId | None = None
    ) -> tuple[KnowledgeNoteRecord, ...]:
        statement = select(KnowledgeNoteTable).where(
            KnowledgeNoteTable.deleted_at.is_(None),
            KnowledgeNoteTable.current_curated_version_id.is_not(None),
            KnowledgeNoteTable.active_index_generation_id.is_not(None),
        )
        if generation_id is not None:
            statement = statement.where(
                KnowledgeNoteTable.active_index_generation_id == generation_id
            )
        rows = await self._session.scalars(
            statement.order_by(KnowledgeNoteTable.canonical_path, KnowledgeNoteTable.id)
        )
        return tuple(to_record(KnowledgeNoteRecord, row) for row in rows)

    async def add_curated_version(
        self,
        record: CuratedVersionRecord,
    ) -> CuratedVersionRecord:
        if record.revision != 1 or record.status is not CuratedVersionStatus.DRAFT:
            raise invariant("curated version creation", record.id)
        await self._note_row(record.note_id)
        await self._required_row(
            KnowledgeChangeTable,
            record.based_on_change_id,
            "knowledge change",
        )
        row = CuratedVersionTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="curated version", identifier=record.id)
        return to_record(CuratedVersionRecord, row)

    async def transition_curated_version(
        self,
        version_id: CuratedVersionId,
        target_status: CuratedVersionStatus,
        *,
        expected_revision: int,
    ) -> CuratedVersionRecord:
        row = await self._curated_row(version_id)
        if row.revision != expected_revision:
            raise concurrent("curated version", version_id)
        require_transition(row.status, target_status)
        updated = await self._update_curated_version(
            version_id,
            target_status,
            expected_revision,
        )
        return to_record(CuratedVersionRecord, updated)

    async def get_curated_version(self, version_id: CuratedVersionId) -> CuratedVersionRecord:
        return to_record(CuratedVersionRecord, await self._curated_row(version_id))

    async def list_curated_versions(
        self, note_id: KnowledgeNoteId
    ) -> tuple[CuratedVersionRecord, ...]:
        await self._note_row(note_id)
        rows = await self._session.scalars(
            select(CuratedVersionTable)
            .where(CuratedVersionTable.note_id == note_id)
            .order_by(CuratedVersionTable.version_number, CuratedVersionTable.id)
        )
        return tuple(to_record(CuratedVersionRecord, row) for row in rows)

    async def activate_curated_version(
        self,
        note_id: KnowledgeNoteId,
        version_id: CuratedVersionId,
        *,
        expected_note_revision: int,
        expected_version_revision: int,
    ) -> tuple[KnowledgeNoteRecord, CuratedVersionRecord]:
        async with self._session.begin_nested():
            note = await self._note_row(note_id)
            if note.revision != expected_note_revision:
                raise concurrent("knowledge note", note_id)
            version = await self._curated_row(version_id)
            if version.note_id != note_id:
                raise invariant("curated version activation", version_id)
            require_transition(version.status, CuratedVersionStatus.ACTIVE)

            active_version = await self._update_curated_version(
                version_id,
                CuratedVersionStatus.ACTIVE,
                expected_version_revision,
            )
            active_note = await self._cas(
                update(KnowledgeNoteTable)
                .where(
                    KnowledgeNoteTable.id == note_id,
                    KnowledgeNoteTable.revision == expected_note_revision,
                    KnowledgeNoteTable.deleted_at.is_(None),
                )
                .values(
                    current_curated_version_id=version_id,
                    revision=expected_note_revision + 1,
                    updated_at=utc_now(),
                )
                .returning(KnowledgeNoteTable),
                select(KnowledgeNoteTable.id).where(KnowledgeNoteTable.id == note_id),
                entity="knowledge note",
                identifier=note_id,
            )
        return (
            to_record(KnowledgeNoteRecord, active_note),
            to_record(CuratedVersionRecord, active_version),
        )

    async def add_lineage_edge(self, record: LineageEdgeRecord) -> LineageEdgeRecord:
        if not await self._entity_exists(record.from_type, record.from_id):
            raise invariant("lineage source", record.id)
        if not await self._entity_exists(record.to_type, record.to_id):
            raise invariant("lineage target", record.id)
        row = LineageEdgeTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="lineage edge", identifier=record.id)
        return to_record(LineageEdgeRecord, row)

    async def add_index_generation(
        self,
        record: IndexGenerationRecord,
    ) -> IndexGenerationRecord:
        if record.revision != 1 or record.status is not IndexGenerationStatus.STAGING:
            raise invariant("index generation creation", record.id)
        row = IndexGenerationTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="index generation", identifier=record.id)
        return to_record(IndexGenerationRecord, row)

    async def transition_index_generation(
        self,
        generation_id: IndexGenerationId,
        target_status: IndexGenerationStatus,
        *,
        expected_revision: int,
    ) -> IndexGenerationRecord:
        row = await self._required_row(
            IndexGenerationTable,
            generation_id,
            "index generation",
        )
        if row.revision != expected_revision:
            raise concurrent("index generation", generation_id)
        require_transition(row.status, target_status)
        values: dict[str, object] = {
            "status": target_status,
            "revision": expected_revision + 1,
        }
        if target_status is IndexGenerationStatus.ACTIVE:
            values["activated_at"] = utc_now()
        updated = await self._cas(
            update(IndexGenerationTable)
            .where(
                IndexGenerationTable.id == generation_id,
                IndexGenerationTable.revision == expected_revision,
            )
            .values(**values)
            .returning(IndexGenerationTable),
            select(IndexGenerationTable.id).where(IndexGenerationTable.id == generation_id),
            entity="index generation",
            identifier=generation_id,
        )
        return to_record(IndexGenerationRecord, updated)

    async def get_index_generation(self, generation_id: IndexGenerationId) -> IndexGenerationRecord:
        row = await self._required_row(IndexGenerationTable, generation_id, "index generation")
        return to_record(IndexGenerationRecord, row)

    async def get_active_index_generation(self) -> IndexGenerationRecord | None:
        row = await self._session.scalar(
            select(IndexGenerationTable).where(
                IndexGenerationTable.status == IndexGenerationStatus.ACTIVE
            )
        )
        return None if row is None else to_record(IndexGenerationRecord, row)

    async def list_index_generations(self) -> tuple[IndexGenerationRecord, ...]:
        rows = await self._session.scalars(
            select(IndexGenerationTable).order_by(
                IndexGenerationTable.generation_number,
                IndexGenerationTable.id,
            )
        )
        return tuple(to_record(IndexGenerationRecord, row) for row in rows)

    async def add_index_job(self, record: IndexJobRecord) -> IndexJobRecord:
        if record.revision != 1 or record.status is not IndexJobStatus.PENDING:
            raise invariant("index job creation", record.id)
        if not await self._entity_exists(record.object_type, record.object_id):
            raise invariant("index job object", record.id)
        await self._required_row(IndexGenerationTable, record.generation_id, "index generation")
        row = IndexJobTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="index job", identifier=record.id)
        return to_record(IndexJobRecord, row)

    async def transition_index_job(
        self,
        job_id: IndexJobId,
        target_status: IndexJobStatus,
        *,
        expected_revision: int,
        error_category: str | None = None,
    ) -> IndexJobRecord:
        row = await self._required_row(IndexJobTable, job_id, "index job")
        if row.revision != expected_revision:
            raise concurrent("index job", job_id)
        require_transition(row.status, target_status)
        updated = await self._cas(
            update(IndexJobTable)
            .where(IndexJobTable.id == job_id, IndexJobTable.revision == expected_revision)
            .values(
                status=target_status,
                error_category=error_category,
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(IndexJobTable),
            select(IndexJobTable.id).where(IndexJobTable.id == job_id),
            entity="index job",
            identifier=job_id,
        )
        return to_record(IndexJobRecord, updated)

    async def get_index_job(self, job_id: IndexJobId) -> IndexJobRecord:
        row = await self._required_row(IndexJobTable, job_id, "index job")
        return to_record(IndexJobRecord, row)

    async def find_index_job(self, operation_id: str) -> IndexJobRecord | None:
        row = await self._session.scalar(
            select(IndexJobTable).where(IndexJobTable.operation_id == operation_id)
        )
        return None if row is None else to_record(IndexJobRecord, row)

    async def mark_index_job_indexed(
        self,
        job_id: IndexJobId,
        *,
        content_hash: str,
        indexed_chunk_count: int,
        expected_revision: int,
    ) -> IndexJobRecord:
        row = await self._required_row(IndexJobTable, job_id, "index job")
        if row.revision != expected_revision:
            raise concurrent("index job", job_id)
        require_transition(row.status, IndexJobStatus.INDEXED)
        updated = await self._cas(
            update(IndexJobTable)
            .where(IndexJobTable.id == job_id, IndexJobTable.revision == expected_revision)
            .values(
                status=IndexJobStatus.INDEXED,
                content_hash=content_hash,
                indexed_chunk_count=indexed_chunk_count,
                last_verified_at=utc_now(),
                error_category=None,
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(IndexJobTable),
            select(IndexJobTable.id).where(IndexJobTable.id == job_id),
            entity="index job",
            identifier=job_id,
        )
        return to_record(IndexJobRecord, updated)

    async def add_publication_run(self, record: PublicationRunRecord) -> PublicationRunRecord:
        if (
            record.revision != 1
            or record.attempt != 1
            or record.status is not PublicationRunStatus.PLANNING
            or record.completed_at is not None
        ):
            raise invariant("publication run creation", record.id)
        change = await self._required_row(
            KnowledgeChangeTable, record.knowledge_change_id, "knowledge change"
        )
        note = await self._note_row(record.note_id)
        version = await self._curated_row(record.curated_version_id)
        await self._required_row(
            IndexGenerationTable, record.target_generation_id, "index generation"
        )
        if version.note_id != note.id or version.based_on_change_id != change.id:
            raise invariant("publication run ownership", record.id)
        row = PublicationRunTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="publication run", identifier=record.id)
        return to_record(PublicationRunRecord, row)

    async def get_publication_run(self, run_id: PublicationRunId) -> PublicationRunRecord:
        row = await self._required_row(PublicationRunTable, run_id, "publication run")
        return to_record(PublicationRunRecord, row)

    async def find_publication_run(self, operation_id: str) -> PublicationRunRecord | None:
        row = await self._session.scalar(
            select(PublicationRunTable).where(PublicationRunTable.operation_id == operation_id)
        )
        return None if row is None else to_record(PublicationRunRecord, row)

    async def transition_publication_run(
        self,
        run_id: PublicationRunId,
        target_status: PublicationRunStatus,
        *,
        expected_revision: int,
        error_category: str | None = None,
    ) -> PublicationRunRecord:
        row = await self._required_row(PublicationRunTable, run_id, "publication run")
        if row.revision != expected_revision:
            raise concurrent("publication run", run_id)
        require_transition(row.status, target_status)
        values: dict[str, object] = {
            "status": target_status,
            "error_category": error_category,
            "revision": expected_revision + 1,
            "updated_at": utc_now(),
        }
        if row.status is PublicationRunStatus.FAILED:
            values["attempt"] = row.attempt + 1
            values["error_category"] = None
        if target_status is PublicationRunStatus.COMPLETED:
            values["completed_at"] = utc_now()
        updated = await self._cas(
            update(PublicationRunTable)
            .where(
                PublicationRunTable.id == run_id,
                PublicationRunTable.revision == expected_revision,
            )
            .values(**values)
            .returning(PublicationRunTable),
            select(PublicationRunTable.id).where(PublicationRunTable.id == run_id),
            entity="publication run",
            identifier=run_id,
        )
        return to_record(PublicationRunRecord, updated)

    async def resolve_current_versions(
        self, note_ids: Sequence[KnowledgeNoteId]
    ) -> dict[KnowledgeNoteId, tuple[CuratedVersionId, IndexGenerationId]]:
        if not note_ids:
            return {}
        rows = await self._session.execute(
            select(
                KnowledgeNoteTable.id,
                KnowledgeNoteTable.current_curated_version_id,
                KnowledgeNoteTable.active_index_generation_id,
            ).where(
                KnowledgeNoteTable.id.in_(tuple(dict.fromkeys(note_ids))),
                KnowledgeNoteTable.deleted_at.is_(None),
                KnowledgeNoteTable.current_curated_version_id.is_not(None),
                KnowledgeNoteTable.active_index_generation_id.is_not(None),
            )
        )
        return {
            row.id: (row.current_curated_version_id, row.active_index_generation_id) for row in rows
        }

    async def activate_publication(
        self,
        *,
        run_id: PublicationRunId,
        job_id: IndexJobId,
        expected_run_revision: int,
        expected_note_revision: int,
        expected_version_revision: int,
        expected_job_revision: int,
        expected_change_revision: int,
        expected_generation_revision: int,
    ) -> tuple[
        PublicationRunRecord,
        KnowledgeNoteRecord,
        CuratedVersionRecord,
        IndexJobRecord,
        KnowledgeChangeRecord,
        IndexGenerationRecord,
    ]:
        """Atomically switch every authoritative pointer after external verification."""

        async with self._session.begin_nested():
            run = await self._required_row(PublicationRunTable, run_id, "publication run")
            note = await self._note_row(run.note_id)
            version = await self._curated_row(run.curated_version_id)
            job = await self._required_row(IndexJobTable, job_id, "index job")
            change = await self._required_row(
                KnowledgeChangeTable, run.knowledge_change_id, "knowledge change"
            )
            generation = await self._required_row(
                IndexGenerationTable, run.target_generation_id, "index generation"
            )
            expected = (
                (run, expected_run_revision, "publication run", run_id),
                (note, expected_note_revision, "knowledge note", note.id),
                (version, expected_version_revision, "curated version", version.id),
                (job, expected_job_revision, "index job", job.id),
                (change, expected_change_revision, "knowledge change", change.id),
                (
                    generation,
                    expected_generation_revision,
                    "index generation",
                    generation.id,
                ),
            )
            for row, revision, entity, identifier in expected:
                if row.revision != revision:
                    raise concurrent(entity, identifier)
            if (
                run.status is not PublicationRunStatus.ACTIVATING
                or version.status is not CuratedVersionStatus.STAGING
                or job.status is not IndexJobStatus.INDEXED
                or change.status is not KnowledgeChangeStatus.PUBLISH_INTENT
                or version.note_id != note.id
                or version.based_on_change_id != change.id
                or job.object_type is not EntityType.CURATED_VERSION
                or job.object_id != version.id
                or job.generation_id != generation.id
            ):
                raise invariant("publication activation", run_id)

            if note.current_curated_version_id is not None:
                old_version = await self._curated_row(note.current_curated_version_id)
                if old_version.id != version.id:
                    require_transition(old_version.status, CuratedVersionStatus.SUPERSEDED)
                    await self._update_curated_version(
                        old_version.id,
                        CuratedVersionStatus.SUPERSEDED,
                        old_version.revision,
                    )

            if generation.status is IndexGenerationStatus.STAGING:
                old_generation = await self._session.scalar(
                    select(IndexGenerationTable).where(
                        IndexGenerationTable.status == IndexGenerationStatus.ACTIVE
                    )
                )
                if old_generation is not None and old_generation.id != generation.id:
                    require_transition(old_generation.status, IndexGenerationStatus.SUPERSEDED)
                    await self._cas(
                        update(IndexGenerationTable)
                        .where(
                            IndexGenerationTable.id == old_generation.id,
                            IndexGenerationTable.revision == old_generation.revision,
                        )
                        .values(
                            status=IndexGenerationStatus.SUPERSEDED,
                            revision=old_generation.revision + 1,
                        )
                        .returning(IndexGenerationTable),
                        select(IndexGenerationTable.id).where(
                            IndexGenerationTable.id == old_generation.id
                        ),
                        entity="index generation",
                        identifier=old_generation.id,
                    )
                active_generation_row = await self._cas(
                    update(IndexGenerationTable)
                    .where(
                        IndexGenerationTable.id == generation.id,
                        IndexGenerationTable.revision == expected_generation_revision,
                    )
                    .values(
                        status=IndexGenerationStatus.ACTIVE,
                        activated_at=utc_now(),
                        revision=expected_generation_revision + 1,
                    )
                    .returning(IndexGenerationTable),
                    select(IndexGenerationTable.id).where(IndexGenerationTable.id == generation.id),
                    entity="index generation",
                    identifier=generation.id,
                )
            elif generation.status is IndexGenerationStatus.ACTIVE:
                active_generation_row = generation
            else:
                raise invariant("publication generation", generation.id)

            now = utc_now()
            active_version_row = await self._cas(
                update(CuratedVersionTable)
                .where(
                    CuratedVersionTable.id == version.id,
                    CuratedVersionTable.revision == expected_version_revision,
                )
                .values(
                    status=CuratedVersionStatus.ACTIVE,
                    published_at=now,
                    revision=expected_version_revision + 1,
                    updated_at=now,
                )
                .returning(CuratedVersionTable),
                select(CuratedVersionTable.id).where(CuratedVersionTable.id == version.id),
                entity="curated version",
                identifier=version.id,
            )
            active_note_row = await self._cas(
                update(KnowledgeNoteTable)
                .where(
                    KnowledgeNoteTable.id == note.id,
                    KnowledgeNoteTable.revision == expected_note_revision,
                    KnowledgeNoteTable.deleted_at.is_(None),
                )
                .values(
                    current_curated_version_id=version.id,
                    active_index_generation_id=generation.id,
                    revision=expected_note_revision + 1,
                    updated_at=now,
                )
                .returning(KnowledgeNoteTable),
                select(KnowledgeNoteTable.id).where(KnowledgeNoteTable.id == note.id),
                entity="knowledge note",
                identifier=note.id,
            )
            active_job_row = await self._cas(
                update(IndexJobTable)
                .where(
                    IndexJobTable.id == job.id,
                    IndexJobTable.revision == expected_job_revision,
                )
                .values(
                    status=IndexJobStatus.ACTIVE_INDEXED,
                    revision=expected_job_revision + 1,
                    updated_at=now,
                )
                .returning(IndexJobTable),
                select(IndexJobTable.id).where(IndexJobTable.id == job.id),
                entity="index job",
                identifier=job.id,
            )
            active_change_row = await self._cas(
                update(KnowledgeChangeTable)
                .where(
                    KnowledgeChangeTable.id == change.id,
                    KnowledgeChangeTable.revision == expected_change_revision,
                )
                .values(
                    status=KnowledgeChangeStatus.ACTIVE,
                    revision=expected_change_revision + 1,
                    updated_at=now,
                )
                .returning(KnowledgeChangeTable),
                select(KnowledgeChangeTable.id).where(KnowledgeChangeTable.id == change.id),
                entity="knowledge change",
                identifier=change.id,
            )
            completed_run_row = await self._cas(
                update(PublicationRunTable)
                .where(
                    PublicationRunTable.id == run.id,
                    PublicationRunTable.revision == expected_run_revision,
                )
                .values(
                    status=PublicationRunStatus.COMPLETED,
                    completed_at=now,
                    error_category=None,
                    revision=expected_run_revision + 1,
                    updated_at=now,
                )
                .returning(PublicationRunTable),
                select(PublicationRunTable.id).where(PublicationRunTable.id == run.id),
                entity="publication run",
                identifier=run.id,
            )
        return (
            to_record(PublicationRunRecord, completed_run_row),
            to_record(KnowledgeNoteRecord, active_note_row),
            to_record(CuratedVersionRecord, active_version_row),
            to_record(IndexJobRecord, active_job_row),
            to_record(KnowledgeChangeRecord, active_change_row),
            to_record(IndexGenerationRecord, active_generation_row),
        )

    async def _note_row(self, note_id: KnowledgeNoteId) -> KnowledgeNoteTable:
        row = await self._session.scalar(
            select(KnowledgeNoteTable).where(
                KnowledgeNoteTable.id == note_id,
                KnowledgeNoteTable.deleted_at.is_(None),
            )
        )
        if row is None:
            raise not_found("knowledge note", note_id)
        return row

    async def _curated_row(self, version_id: CuratedVersionId) -> CuratedVersionTable:
        return await self._required_row(CuratedVersionTable, version_id, "curated version")

    async def _update_curated_version(
        self,
        version_id: CuratedVersionId,
        status: CuratedVersionStatus,
        expected_revision: int,
    ) -> CuratedVersionTable:
        return cast(
            CuratedVersionTable,
            await self._cas(
                update(CuratedVersionTable)
                .where(
                    CuratedVersionTable.id == version_id,
                    CuratedVersionTable.revision == expected_revision,
                )
                .values(
                    status=status,
                    revision=expected_revision + 1,
                    updated_at=utc_now(),
                )
                .returning(CuratedVersionTable),
                select(CuratedVersionTable.id).where(CuratedVersionTable.id == version_id),
                entity="curated version",
                identifier=version_id,
            ),
        )

    async def _entity_exists(self, entity_type: EntityType, entity_id: TypedId) -> bool:
        table = Base.metadata.tables[ENTITY_TABLE_NAMES[entity_type]]
        return (
            await self._session.scalar(select(table.c.id).where(table.c.id == entity_id))
            is not None
        )

    async def _required_row[RowT, IdT: TypedId](
        self,
        table: type[RowT],
        identifier: IdT,
        entity: str,
    ) -> RowT:
        row = await self._session.get(table, identifier)
        if row is None:
            raise not_found(entity, identifier)
        return row

    async def _cas(
        self,
        statement: Update,
        exists_statement: Executable,
        *,
        entity: str,
        identifier: TypedId,
    ) -> object:
        try:
            result = await self._session.execute(statement)
        except IntegrityError as error:
            raise_constraint_error(error, entity=entity, identifier=identifier)
        except OperationalError as error:
            raise_operational_error(error)
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        exists = (await self._session.execute(exists_statement)).scalar_one_or_none()
        if exists is None:
            raise not_found(entity, identifier)
        raise concurrent(entity, identifier)


__all__ = ["PublicationRepository"]
