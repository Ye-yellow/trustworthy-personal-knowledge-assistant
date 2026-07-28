"""Async repository for source lineage and source-version state."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from trustworthy_kb.domain import (
    ContentBlockRecord,
    SourceId,
    SourceRecord,
    SourceVersionId,
    SourceVersionRecord,
    SourceVersionStatus,
    require_transition,
)
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.persistence.ingestion_tables import SourceLocationTable
from trustworthy_kb.persistence.repository_base import (
    concurrent,
    flush_safely,
    invariant,
    not_found,
    raise_constraint_error,
    raise_operational_error,
    to_record,
)
from trustworthy_kb.persistence.source_tables import (
    ContentBlockTable,
    SourceTable,
    SourceVersionTable,
)


class SourceRepository:
    """Persist source records without exposing mutable ORM objects."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_source(self, record: SourceRecord) -> SourceRecord:
        if (
            record.revision != 1
            or record.current_version_id is not None
            or record.deleted_at is not None
        ):
            raise invariant("source creation", record.id)
        row = SourceTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="source", identifier=record.id)
        return to_record(SourceRecord, row)

    async def get_source(
        self,
        source_id: SourceId,
        *,
        include_deleted: bool = False,
    ) -> SourceRecord:
        statement = select(SourceTable).where(SourceTable.id == source_id)
        if not include_deleted:
            statement = statement.where(SourceTable.deleted_at.is_(None))
        row = await self._session.scalar(statement)
        if row is None:
            raise not_found("source", source_id)
        return to_record(SourceRecord, row)

    async def find_source_by_location(
        self,
        vault_id_hash: str,
        path_key: str,
    ) -> SourceRecord | None:
        row = await self._session.scalar(
            select(SourceTable)
            .join(SourceLocationTable, SourceLocationTable.source_id == SourceTable.id)
            .where(
                SourceLocationTable.vault_id_hash == vault_id_hash,
                SourceLocationTable.path_key == path_key,
                SourceLocationTable.deleted_at.is_(None),
                SourceTable.deleted_at.is_(None),
            )
        )
        return None if row is None else to_record(SourceRecord, row)

    async def list_live_sources_for_vault(self, vault_id_hash: str) -> tuple[SourceRecord, ...]:
        rows = await self._session.scalars(
            select(SourceTable)
            .join(SourceLocationTable, SourceLocationTable.source_id == SourceTable.id)
            .where(
                SourceLocationTable.vault_id_hash == vault_id_hash,
                SourceLocationTable.deleted_at.is_(None),
                SourceTable.deleted_at.is_(None),
            )
            .order_by(SourceLocationTable.path_key)
        )
        return tuple(to_record(SourceRecord, row) for row in rows)

    async def append_source_version(self, record: SourceVersionRecord) -> SourceVersionRecord:
        await self.get_source(record.source_id)
        if record.revision != 1 or record.status is not SourceVersionStatus.CAPTURED:
            raise invariant("source version creation", record.id)
        row = SourceVersionTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="source version", identifier=record.id)
        return to_record(SourceVersionRecord, row)

    async def add_content_blocks(
        self,
        records: Sequence[ContentBlockRecord],
    ) -> tuple[ContentBlockRecord, ...]:
        if not records:
            return ()
        rows = [ContentBlockTable(**record.model_dump(mode="python")) for record in records]
        self._session.add_all(rows)
        await flush_safely(self._session, entity="content block", identifier=records[0].id)
        return tuple(to_record(ContentBlockRecord, row) for row in rows)

    async def list_content_blocks(
        self,
        version_id: SourceVersionId,
    ) -> tuple[ContentBlockRecord, ...]:
        await self.get_source_version(version_id)
        rows = await self._session.scalars(
            select(ContentBlockTable)
            .where(ContentBlockTable.source_version_id == version_id)
            .order_by(ContentBlockTable.ordinal)
        )
        return tuple(to_record(ContentBlockRecord, row) for row in rows)

    async def get_source_version(self, version_id: SourceVersionId) -> SourceVersionRecord:
        return to_record(SourceVersionRecord, await self._source_version_row(version_id))

    async def get_current_source_version(
        self,
        source_id: SourceId,
    ) -> SourceVersionRecord | None:
        source = await self.get_source(source_id)
        if source.current_version_id is None:
            return None
        return await self.get_source_version(source.current_version_id)

    async def get_latest_source_version(
        self,
        source_id: SourceId,
    ) -> SourceVersionRecord | None:
        await self.get_source(source_id)
        row = await self._session.scalar(
            select(SourceVersionTable)
            .where(SourceVersionTable.source_id == source_id)
            .order_by(SourceVersionTable.version_number.desc())
            .limit(1)
        )
        return None if row is None else to_record(SourceVersionRecord, row)

    async def find_source_version_by_hash(
        self,
        source_id: SourceId,
        content_hash: str,
    ) -> SourceVersionRecord | None:
        await self.get_source(source_id)
        row = await self._session.scalar(
            select(SourceVersionTable).where(
                SourceVersionTable.source_id == source_id,
                SourceVersionTable.content_hash == content_hash,
            )
        )
        return None if row is None else to_record(SourceVersionRecord, row)

    async def transition_source_version(
        self,
        version_id: SourceVersionId,
        target_status: SourceVersionStatus,
        *,
        expected_revision: int,
    ) -> SourceVersionRecord:
        row = await self._source_version_row(version_id)
        if row.revision != expected_revision:
            raise concurrent("source version", version_id)
        require_transition(row.status, target_status)
        return await self._update_source_version(
            version_id,
            expected_revision,
            status=target_status,
        )

    async def activate_source_version(
        self,
        source_id: SourceId,
        version_id: SourceVersionId,
        *,
        expected_revision: int,
    ) -> SourceRecord:
        source = await self.get_source(source_id)
        if source.revision != expected_revision:
            raise concurrent("source", source_id)
        version = await self._source_version_row(version_id)
        if version.source_id != source_id or version.status is not SourceVersionStatus.READY:
            raise invariant("source version activation", version_id)
        return await self._update_source(
            source_id,
            expected_revision,
            current_version_id=version_id,
        )

    async def mark_source_deleted(
        self,
        source_id: SourceId,
        *,
        expected_revision: int,
        deleted_at: datetime | None = None,
    ) -> SourceRecord:
        source = await self.get_source(source_id)
        if source.revision != expected_revision:
            raise concurrent("source", source_id)
        return await self._update_source(
            source_id,
            expected_revision,
            deleted_at=deleted_at or utc_now(),
        )

    async def move_source(
        self,
        source_id: SourceId,
        canonical_uri: str,
        *,
        expected_revision: int,
    ) -> SourceRecord:
        source = await self.get_source(source_id)
        if source.revision != expected_revision or not canonical_uri.strip():
            if source.revision != expected_revision:
                raise concurrent("source", source_id)
            raise invariant("source move", source_id)
        return await self._update_source(
            source_id,
            expected_revision,
            canonical_uri=canonical_uri,
        )

    async def _source_version_row(self, version_id: SourceVersionId) -> SourceVersionTable:
        row = await self._session.get(SourceVersionTable, version_id)
        if row is None:
            raise not_found("source version", version_id)
        return row

    async def _update_source_version(
        self,
        version_id: SourceVersionId,
        expected_revision: int,
        **values: object,
    ) -> SourceVersionRecord:
        statement = (
            update(SourceVersionTable)
            .where(
                SourceVersionTable.id == version_id,
                SourceVersionTable.revision == expected_revision,
            )
            .values(**values, revision=expected_revision + 1, updated_at=utc_now())
            .returning(SourceVersionTable)
        )
        row = await self._execute_cas(
            statement,
            entity="source version",
            identifier=version_id,
            table=SourceVersionTable,
        )
        return to_record(SourceVersionRecord, row)

    async def _update_source(
        self,
        source_id: SourceId,
        expected_revision: int,
        **values: object,
    ) -> SourceRecord:
        statement = (
            update(SourceTable)
            .where(SourceTable.id == source_id, SourceTable.revision == expected_revision)
            .values(**values, revision=expected_revision + 1, updated_at=utc_now())
            .returning(SourceTable)
        )
        row = await self._execute_cas(
            statement,
            entity="source",
            identifier=source_id,
            table=SourceTable,
        )
        return to_record(SourceRecord, row)

    async def _execute_cas(
        self,
        statement: Update,
        *,
        entity: str,
        identifier: SourceId | SourceVersionId,
        table: type[SourceTable] | type[SourceVersionTable],
    ) -> SourceTable | SourceVersionTable:
        try:
            result = await self._session.execute(statement)
        except IntegrityError as error:
            raise_constraint_error(error, entity=entity, identifier=identifier)
        except OperationalError as error:
            raise_operational_error(error)
        row = result.scalar_one_or_none()
        if row is not None:
            return cast(SourceTable | SourceVersionTable, row)
        exists = await self._session.scalar(select(table.id).where(table.id == identifier))
        if exists is None:
            raise not_found(entity, identifier)
        raise concurrent(entity, identifier)


__all__ = ["SourceRepository"]
