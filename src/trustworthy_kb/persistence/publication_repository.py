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
