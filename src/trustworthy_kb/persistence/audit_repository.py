"""Async repository for model runs, audit chains, and idempotency leases."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.base import Executable
from sqlalchemy.sql.dml import Update

from trustworthy_kb.domain import (
    EntityType,
    IdempotencyRecord,
    IdempotencyRecordId,
    IdempotencyStatus,
    ModelRunId,
    ModelRunRecord,
    ModelRunStatus,
    OperationLogRecord,
    TypedId,
    operation_log_entry_hash,
    require_transition,
)
from trustworthy_kb.domain.base import AwareDatetime, Sha256Hex
from trustworthy_kb.persistence.audit_tables import (
    IdempotencyRecordTable,
    ModelRunTable,
    OperationLogTable,
)
from trustworthy_kb.persistence.base import ENTITY_TABLE_NAMES, Base, utc_now
from trustworthy_kb.persistence.errors import (
    DuplicateRecordError,
    IdempotencyConflictError,
    OperationInProgressError,
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


class AuditRepository:
    """Persist privacy-minimized execution and audit metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start_model_run(self, record: ModelRunRecord) -> ModelRunRecord:
        if record.revision != 1 or record.status is not ModelRunStatus.STARTED:
            raise invariant("model run start", record.id)
        row = ModelRunTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="model run", identifier=record.id)
        return to_record(ModelRunRecord, row)

    async def finish_model_run(
        self,
        model_run_id: ModelRunId,
        target_status: ModelRunStatus,
        *,
        expected_revision: int,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int | None,
        completed_at: AwareDatetime,
        output_hash: Sha256Hex | None = None,
        request_id: str | None = None,
        error_category: str | None = None,
    ) -> ModelRunRecord:
        row = await self._required_model_run(model_run_id)
        if row.revision != expected_revision:
            raise concurrent("model run", model_run_id)
        require_transition(row.status, target_status)
        if target_status is ModelRunStatus.SUCCEEDED and output_hash is None:
            raise invariant("model run completion", model_run_id)
        updated = await self._cas(
            update(ModelRunTable)
            .where(
                ModelRunTable.id == model_run_id,
                ModelRunTable.revision == expected_revision,
            )
            .values(
                status=target_status,
                output_hash=output_hash,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency_ms,
                request_id=request_id,
                error_category=error_category,
                completed_at=completed_at,
                revision=expected_revision + 1,
            )
            .returning(ModelRunTable),
            select(ModelRunTable.id).where(ModelRunTable.id == model_run_id),
            entity="model run",
            identifier=model_run_id,
        )
        return to_record(ModelRunRecord, updated)

    async def append_operation_log(self, record: OperationLogRecord) -> OperationLogRecord:
        if not await self._entity_exists(record.target_type, record.target_id):
            raise invariant("operation log target", record.id)
        previous = await self._session.scalar(
            select(OperationLogTable)
            .where(OperationLogTable.operation_id == record.operation_id)
            .order_by(OperationLogTable.step_number.desc())
            .limit(1)
        )
        expected_step = 0 if previous is None else previous.step_number + 1
        expected_previous_hash = None if previous is None else previous.entry_hash
        if (
            record.step_number != expected_step
            or record.previous_entry_hash != expected_previous_hash
        ):
            raise invariant("operation log chain", record.id)
        computed_hash = operation_log_entry_hash(
            operation_id=record.operation_id,
            step_number=record.step_number,
            actor_type=record.actor_type,
            actor_id=record.actor_id,
            action=record.action,
            target_type=record.target_type,
            target_id=record.target_id,
            before_json=record.before_json,
            after_json=record.after_json,
            previous_entry_hash=record.previous_entry_hash,
            created_at=record.created_at,
        )
        if record.entry_hash != computed_hash:
            raise invariant("operation log hash", record.id)
        row = OperationLogTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="operation log", identifier=record.id)
        return to_record(OperationLogRecord, row)

    async def acquire_idempotency_key(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_hash: Sha256Hex,
        lease_owner: str,
        lease_duration: timedelta,
        now: AwareDatetime | None = None,
        expires_at: AwareDatetime | None = None,
    ) -> IdempotencyRecord:
        acquired_at = now or utc_now()
        if lease_duration <= timedelta(0):
            raise invariant("idempotency lease", idempotency_key)
        lease_expires_at = acquired_at + lease_duration
        record = IdempotencyRecord(
            id=IdempotencyRecordId.generate(),
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status=IdempotencyStatus.IN_PROGRESS,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            attempt=1,
            revision=1,
            created_at=acquired_at,
            updated_at=acquired_at,
            expires_at=expires_at,
        )
        try:
            async with self._session.begin_nested():
                row = IdempotencyRecordTable(**record.model_dump(mode="python"))
                self._session.add(row)
                await flush_safely(self._session, entity="idempotency", identifier=record.id)
            return to_record(IdempotencyRecord, row)
        except DuplicateRecordError:
            existing = await self._session.scalar(
                select(IdempotencyRecordTable).where(
                    IdempotencyRecordTable.scope == scope,
                    IdempotencyRecordTable.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise

        if existing.request_hash != request_hash:
            raise IdempotencyConflictError("idempotency key conflicts with a different request")
        if existing.status is IdempotencyStatus.SUCCEEDED:
            return to_record(IdempotencyRecord, existing)
        if existing.status is IdempotencyStatus.UNKNOWN:
            raise OperationInProgressError("idempotent operation requires reconciliation")
        if existing.status is IdempotencyStatus.FAILED:
            raise OperationInProgressError("idempotent operation is terminal")
        if existing.lease_expires_at is not None and existing.lease_expires_at > acquired_at:
            raise OperationInProgressError("idempotent operation has an active lease")

        taken_over = await self._cas(
            update(IdempotencyRecordTable)
            .where(
                IdempotencyRecordTable.id == existing.id,
                IdempotencyRecordTable.revision == existing.revision,
                IdempotencyRecordTable.status == IdempotencyStatus.IN_PROGRESS,
            )
            .values(
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                attempt=existing.attempt + 1,
                revision=existing.revision + 1,
                updated_at=acquired_at,
            )
            .returning(IdempotencyRecordTable),
            select(IdempotencyRecordTable.id).where(IdempotencyRecordTable.id == existing.id),
            entity="idempotency",
            identifier=existing.id,
        )
        return to_record(IdempotencyRecord, taken_over)

    async def complete_idempotent_operation(
        self,
        record_id: IdempotencyRecordId,
        *,
        result_type: EntityType,
        result_id: TypedId,
        expected_revision: int,
        reconcile_unknown: bool = False,
    ) -> IdempotencyRecord:
        if not await self._entity_exists(result_type, result_id):
            raise invariant("idempotency result", record_id)
        return await self._finish_idempotency(
            record_id,
            IdempotencyStatus.SUCCEEDED,
            expected_revision=expected_revision,
            reconcile_unknown=reconcile_unknown,
            result_type=result_type,
            result_id=result_id,
            error_category=None,
        )

    async def fail_idempotent_operation(
        self,
        record_id: IdempotencyRecordId,
        *,
        expected_revision: int,
        error_category: str,
        reconcile_unknown: bool = False,
    ) -> IdempotencyRecord:
        return await self._finish_idempotency(
            record_id,
            IdempotencyStatus.FAILED,
            expected_revision=expected_revision,
            reconcile_unknown=reconcile_unknown,
            result_type=None,
            result_id=None,
            error_category=error_category,
        )

    async def mark_idempotent_operation_unknown(
        self,
        record_id: IdempotencyRecordId,
        *,
        expected_revision: int,
        error_category: str,
    ) -> IdempotencyRecord:
        return await self._finish_idempotency(
            record_id,
            IdempotencyStatus.UNKNOWN,
            expected_revision=expected_revision,
            reconcile_unknown=False,
            result_type=None,
            result_id=None,
            error_category=error_category,
        )

    async def _finish_idempotency(
        self,
        record_id: IdempotencyRecordId,
        target_status: IdempotencyStatus,
        *,
        expected_revision: int,
        reconcile_unknown: bool,
        result_type: EntityType | None,
        result_id: TypedId | None,
        error_category: str | None,
    ) -> IdempotencyRecord:
        row = await self._required_idempotency(record_id)
        if row.revision != expected_revision:
            raise concurrent("idempotency", record_id)
        if row.status is IdempotencyStatus.UNKNOWN and not reconcile_unknown:
            raise invariant("idempotency reconciliation", record_id)
        require_transition(row.status, target_status)
        updated = await self._cas(
            update(IdempotencyRecordTable)
            .where(
                IdempotencyRecordTable.id == record_id,
                IdempotencyRecordTable.revision == expected_revision,
            )
            .values(
                status=target_status,
                result_type=result_type,
                result_id=result_id,
                lease_owner=None,
                lease_expires_at=None,
                error_category=error_category,
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(IdempotencyRecordTable),
            select(IdempotencyRecordTable.id).where(IdempotencyRecordTable.id == record_id),
            entity="idempotency",
            identifier=record_id,
        )
        return to_record(IdempotencyRecord, updated)

    async def _entity_exists(self, entity_type: EntityType, entity_id: TypedId) -> bool:
        table = Base.metadata.tables[ENTITY_TABLE_NAMES[entity_type]]
        return (
            await self._session.scalar(select(table.c.id).where(table.c.id == entity_id))
            is not None
        )

    async def _required_model_run(self, model_run_id: ModelRunId) -> ModelRunTable:
        row = await self._session.get(ModelRunTable, model_run_id)
        if row is None:
            raise not_found("model run", model_run_id)
        return row

    async def _required_idempotency(
        self,
        record_id: IdempotencyRecordId,
    ) -> IdempotencyRecordTable:
        row = await self._session.get(IdempotencyRecordTable, record_id)
        if row is None:
            raise not_found("idempotency", record_id)
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


__all__ = ["AuditRepository"]
