"""Async repository for ingestion runs, items, and source locations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from trustworthy_kb.domain import (
    IngestionItemId,
    IngestionItemRecord,
    IngestionItemStatus,
    IngestionRunId,
    IngestionRunRecord,
    IngestionRunStatus,
    IngestionRunSummary,
    SourceId,
    SourceLocationRecord,
    SourceVersionId,
    require_transition,
)
from trustworthy_kb.ingestion.errors import IngestionAlreadyRunningError
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.persistence.errors import DuplicateRecordError
from trustworthy_kb.persistence.ingestion_tables import (
    IngestionItemTable,
    IngestionRunTable,
    SourceLocationTable,
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

_TERMINAL_ITEM_STATUSES = frozenset(
    {
        IngestionItemStatus.SUCCEEDED,
        IngestionItemStatus.SKIPPED,
        IngestionItemStatus.QUARANTINED,
        IngestionItemStatus.FAILED,
    }
)


class IngestionRepository:
    """Persist ingestion state without committing the Unit of Work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def begin_run(self, record: IngestionRunRecord) -> IngestionRunRecord:
        if record.revision != 1 or record.status is not IngestionRunStatus.PLANNING:
            raise invariant("ingestion run creation", record.id)
        try:
            async with self._session.begin_nested():
                row = IngestionRunTable(**record.model_dump(mode="python"))
                self._session.add(row)
                await flush_safely(self._session, entity="ingestion run", identifier=record.id)
        except DuplicateRecordError:
            active_run_id = await self._session.scalar(
                select(IngestionRunTable.id).where(
                    IngestionRunTable.vault_id_hash == record.vault_id_hash,
                    IngestionRunTable.status.in_(
                        (IngestionRunStatus.PLANNING, IngestionRunStatus.APPLYING)
                    ),
                )
            )
            if active_run_id is not None:
                raise IngestionAlreadyRunningError("an ingestion run is already active") from None
            raise
        return to_record(IngestionRunRecord, row)

    async def save_plan(
        self,
        run_id: IngestionRunId,
        manifest_hash: str,
        records: Sequence[IngestionItemRecord],
        *,
        expected_run_revision: int,
    ) -> tuple[IngestionRunRecord, tuple[IngestionItemRecord, ...]]:
        run = await self.get_run(run_id)
        if run.revision != expected_run_revision:
            raise concurrent("ingestion run", run_id)
        if run.status is not IngestionRunStatus.PLANNING:
            raise invariant("ingestion plan", run_id)
        if any(
            record.run_id != run_id
            or record.revision != 1
            or record.status is not IngestionItemStatus.PENDING
            for record in records
        ):
            raise invariant("ingestion plan", run_id)
        rows = [IngestionItemTable(**record.model_dump(mode="python")) for record in records]
        self._session.add_all(rows)
        if rows:
            await flush_safely(self._session, entity="ingestion item", identifier=records[0].id)
        updated_run = await self._update_run(
            run_id,
            expected_run_revision,
            manifest_hash=manifest_hash,
            total_items=len(records),
        )
        return updated_run, tuple(to_record(IngestionItemRecord, row) for row in rows)

    async def get_run(self, run_id: IngestionRunId) -> IngestionRunRecord:
        row = await self._session.get(IngestionRunTable, run_id)
        if row is None:
            raise not_found("ingestion run", run_id)
        return to_record(IngestionRunRecord, row)

    async def get_item(self, item_id: IngestionItemId) -> IngestionItemRecord:
        row = await self._session.get(IngestionItemTable, item_id)
        if row is None:
            raise not_found("ingestion item", item_id)
        return to_record(IngestionItemRecord, row)

    async def list_pending_items(self, run_id: IngestionRunId) -> tuple[IngestionItemRecord, ...]:
        rows = await self._session.scalars(
            select(IngestionItemTable)
            .where(
                IngestionItemTable.run_id == run_id,
                IngestionItemTable.status == IngestionItemStatus.PENDING,
            )
            .order_by(IngestionItemTable.path_key, IngestionItemTable.action)
        )
        return tuple(to_record(IngestionItemRecord, row) for row in rows)

    async def start_item(
        self,
        item_id: IngestionItemId,
        *,
        expected_revision: int,
    ) -> IngestionItemRecord:
        item = await self.get_item(item_id)
        if item.revision != expected_revision:
            raise concurrent("ingestion item", item_id)
        require_transition(item.status, IngestionItemStatus.APPLYING)
        return await self._update_item(
            item_id,
            expected_revision,
            status=IngestionItemStatus.APPLYING,
            completed_at=None,
            error_category=None,
        )

    async def retry_item(
        self,
        item_id: IngestionItemId,
        *,
        operation_id: str,
        expected_revision: int,
    ) -> IngestionItemRecord:
        item = await self.get_item(item_id)
        if item.revision != expected_revision:
            raise concurrent("ingestion item", item_id)
        require_transition(item.status, IngestionItemStatus.PENDING)
        return await self._update_item(
            item_id,
            expected_revision,
            status=IngestionItemStatus.PENDING,
            operation_id=operation_id,
            attempt=item.attempt + 1,
            completed_at=None,
            error_category=None,
        )

    async def record_source_location(
        self,
        record: SourceLocationRecord,
    ) -> SourceLocationRecord:
        if record.revision != 1 or record.deleted_at is not None:
            raise invariant("source location creation", record.source_id)
        row = SourceLocationTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="source location", identifier=record.source_id)
        return to_record(SourceLocationRecord, row)

    async def get_source_location(
        self,
        source_id: SourceId,
        *,
        include_deleted: bool = False,
    ) -> SourceLocationRecord:
        statement = select(SourceLocationTable).where(SourceLocationTable.source_id == source_id)
        if not include_deleted:
            statement = statement.where(SourceLocationTable.deleted_at.is_(None))
        row = await self._session.scalar(statement)
        if row is None:
            raise not_found("source location", source_id)
        return to_record(SourceLocationRecord, row)

    async def list_live_source_locations(
        self,
        vault_id_hash: str,
    ) -> tuple[SourceLocationRecord, ...]:
        rows = await self._session.scalars(
            select(SourceLocationTable)
            .where(
                SourceLocationTable.vault_id_hash == vault_id_hash,
                SourceLocationTable.deleted_at.is_(None),
            )
            .order_by(SourceLocationTable.path_key)
        )
        return tuple(to_record(SourceLocationRecord, row) for row in rows)

    async def move_source_location(
        self,
        source_id: SourceId,
        *,
        relative_path: str,
        path_key: str,
        file_key: str | None,
        last_seen_run_id: IngestionRunId,
        observed_size: int,
        observed_mtime_ns: int,
        expected_revision: int,
    ) -> SourceLocationRecord:
        await self.get_source_location(source_id)
        return await self._update_location(
            source_id,
            expected_revision,
            relative_path=relative_path,
            path_key=path_key,
            file_key=file_key,
            last_seen_run_id=last_seen_run_id,
            observed_size=observed_size,
            observed_mtime_ns=observed_mtime_ns,
        )

    async def touch_source_location(
        self,
        source_id: SourceId,
        *,
        file_key: str | None,
        last_seen_run_id: IngestionRunId,
        observed_size: int,
        observed_mtime_ns: int,
        expected_revision: int,
    ) -> SourceLocationRecord:
        await self.get_source_location(source_id)
        return await self._update_location(
            source_id,
            expected_revision,
            file_key=file_key,
            last_seen_run_id=last_seen_run_id,
            observed_size=observed_size,
            observed_mtime_ns=observed_mtime_ns,
        )

    async def mark_source_location_deleted(
        self,
        source_id: SourceId,
        *,
        expected_revision: int,
        deleted_at: datetime | None = None,
    ) -> SourceLocationRecord:
        await self.get_source_location(source_id)
        return await self._update_location(
            source_id,
            expected_revision,
            deleted_at=deleted_at or utc_now(),
        )

    async def complete_item(
        self,
        item_id: IngestionItemId,
        target_status: IngestionItemStatus,
        *,
        expected_revision: int,
        source_id: SourceId | None = None,
        result_version_id: SourceVersionId | None = None,
        safety_signals: Mapping[str, int] | None = None,
    ) -> IngestionItemRecord:
        if target_status not in _TERMINAL_ITEM_STATUSES - {IngestionItemStatus.FAILED}:
            raise invariant("ingestion item completion", item_id)
        item = await self.get_item(item_id)
        if item.revision != expected_revision:
            raise concurrent("ingestion item", item_id)
        require_transition(item.status, target_status)
        return await self._update_item(
            item_id,
            expected_revision,
            status=target_status,
            source_id=source_id if source_id is not None else item.source_id,
            result_version_id=(
                result_version_id if result_version_id is not None else item.result_version_id
            ),
            safety_signals_json=dict(safety_signals or {}),
            completed_at=utc_now(),
            error_category=None,
        )

    async def fail_item(
        self,
        item_id: IngestionItemId,
        *,
        error_category: str,
        expected_revision: int,
    ) -> IngestionItemRecord:
        if not error_category.strip():
            raise invariant("ingestion item failure", item_id)
        item = await self.get_item(item_id)
        if item.revision != expected_revision:
            raise concurrent("ingestion item", item_id)
        require_transition(item.status, IngestionItemStatus.FAILED)
        return await self._update_item(
            item_id,
            expected_revision,
            status=IngestionItemStatus.FAILED,
            error_category=error_category,
            completed_at=utc_now(),
        )

    async def transition_run(
        self,
        run_id: IngestionRunId,
        target_status: IngestionRunStatus,
        *,
        expected_revision: int,
        error_category: str | None = None,
    ) -> IngestionRunRecord:
        run = await self.get_run(run_id)
        if run.revision != expected_revision:
            raise concurrent("ingestion run", run_id)
        require_transition(run.status, target_status)
        summary = await self.summarize_run(run_id)
        if target_status is IngestionRunStatus.COMPLETED and (
            summary.failed or summary.quarantined or summary.pending or summary.applying
        ):
            raise invariant("ingestion run completion", run_id)
        if target_status is IngestionRunStatus.PARTIAL_FAILED and (
            not (summary.failed or summary.quarantined) or summary.pending or summary.applying
        ):
            raise invariant("ingestion run completion", run_id)
        terminal = target_status not in {IngestionRunStatus.PLANNING, IngestionRunStatus.APPLYING}
        return await self._update_run(
            run_id,
            expected_revision,
            status=target_status,
            total_items=summary.total,
            succeeded_items=summary.succeeded,
            skipped_items=summary.skipped,
            quarantined_items=summary.quarantined,
            failed_items=summary.failed,
            error_category=error_category,
            completed_at=utc_now() if terminal else None,
        )

    async def summarize_run(self, run_id: IngestionRunId) -> IngestionRunSummary:
        await self.get_run(run_id)
        rows = await self._session.execute(
            select(IngestionItemTable.status, func.count(IngestionItemTable.id))
            .where(IngestionItemTable.run_id == run_id)
            .group_by(IngestionItemTable.status)
        )
        counts = Counter({status: count for status, count in rows})
        return IngestionRunSummary(
            total=sum(counts.values()),
            pending=counts[IngestionItemStatus.PENDING],
            applying=counts[IngestionItemStatus.APPLYING],
            succeeded=counts[IngestionItemStatus.SUCCEEDED],
            skipped=counts[IngestionItemStatus.SKIPPED],
            quarantined=counts[IngestionItemStatus.QUARANTINED],
            failed=counts[IngestionItemStatus.FAILED],
        )

    async def _update_run(
        self,
        run_id: IngestionRunId,
        expected_revision: int,
        **values: object,
    ) -> IngestionRunRecord:
        row = await self._execute_cas(
            update(IngestionRunTable)
            .where(
                IngestionRunTable.id == run_id,
                IngestionRunTable.revision == expected_revision,
            )
            .values(**values, revision=expected_revision + 1, updated_at=utc_now())
            .returning(IngestionRunTable),
            table=IngestionRunTable,
            entity="ingestion run",
            identifier=run_id,
        )
        return to_record(IngestionRunRecord, row)

    async def _update_item(
        self,
        item_id: IngestionItemId,
        expected_revision: int,
        **values: object,
    ) -> IngestionItemRecord:
        row = await self._execute_cas(
            update(IngestionItemTable)
            .where(
                IngestionItemTable.id == item_id,
                IngestionItemTable.revision == expected_revision,
            )
            .values(**values, revision=expected_revision + 1, updated_at=utc_now())
            .returning(IngestionItemTable),
            table=IngestionItemTable,
            entity="ingestion item",
            identifier=item_id,
        )
        return to_record(IngestionItemRecord, row)

    async def _update_location(
        self,
        source_id: SourceId,
        expected_revision: int,
        **values: object,
    ) -> SourceLocationRecord:
        row = await self._execute_cas(
            update(SourceLocationTable)
            .where(
                SourceLocationTable.source_id == source_id,
                SourceLocationTable.revision == expected_revision,
            )
            .values(**values, revision=expected_revision + 1, updated_at=utc_now())
            .returning(SourceLocationTable),
            table=SourceLocationTable,
            entity="source location",
            identifier=source_id,
        )
        return to_record(SourceLocationRecord, row)

    async def _execute_cas(
        self,
        statement: Update,
        *,
        table: type[IngestionRunTable] | type[IngestionItemTable] | type[SourceLocationTable],
        entity: str,
        identifier: IngestionRunId | IngestionItemId | SourceId,
    ) -> IngestionRunTable | IngestionItemTable | SourceLocationTable:
        try:
            result = await self._session.execute(statement)
        except IntegrityError as error:
            raise_constraint_error(error, entity=entity, identifier=identifier)
        except OperationalError as error:
            raise_operational_error(error)
        row = result.scalar_one_or_none()
        if row is not None:
            return cast(IngestionRunTable | IngestionItemTable | SourceLocationTable, row)
        if table is SourceLocationTable:
            exists = await self._session.scalar(
                select(SourceLocationTable.source_id).where(
                    SourceLocationTable.source_id == identifier
                )
            )
        elif table is IngestionRunTable:
            exists = await self._session.scalar(
                select(IngestionRunTable.id).where(IngestionRunTable.id == identifier)
            )
        else:
            exists = await self._session.scalar(
                select(IngestionItemTable.id).where(IngestionItemTable.id == identifier)
            )
        if exists is None:
            raise not_found(entity, identifier)
        raise concurrent(entity, identifier)


__all__ = ["IngestionRepository"]
