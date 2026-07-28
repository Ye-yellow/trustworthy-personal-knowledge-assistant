"""Async repository for privacy-preserving trusted answer runs."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from trustworthy_kb.domain import (
    AnswerRunId,
    AnswerRunRecord,
    AnswerRunStatus,
    IndexGenerationId,
    require_transition,
)
from trustworthy_kb.persistence.answer_tables import AnswerRunTable
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.persistence.publication_tables import IndexGenerationTable
from trustworthy_kb.persistence.repository_base import (
    concurrent,
    flush_safely,
    invariant,
    not_found,
    raise_constraint_error,
    raise_operational_error,
    to_record,
)


class AnswerRepository:
    """Persist only hashes, lineage pointers, and stable terminal outcomes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_run(self, record: AnswerRunRecord) -> AnswerRunRecord:
        if (
            record.revision != 1
            or record.status is not AnswerRunStatus.IN_PROGRESS
            or record.completed_at is not None
            or any(
                value is not None
                for value in (
                    record.generation_id,
                    record.plan_hash,
                    record.refusal_code,
                    record.answer_hash,
                    record.citation_manifest_hash,
                )
            )
        ):
            raise invariant("answer run creation", record.id)
        row = AnswerRunTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="answer run", identifier=record.id)
        return to_record(AnswerRunRecord, row)

    async def get_run(self, run_id: AnswerRunId) -> AnswerRunRecord:
        return to_record(AnswerRunRecord, await self._required(run_id))

    async def find_run(self, operation_id: str) -> AnswerRunRecord | None:
        row = await self._session.scalar(
            select(AnswerRunTable).where(AnswerRunTable.operation_id == operation_id)
        )
        return None if row is None else to_record(AnswerRunRecord, row)

    async def complete_answer(
        self,
        run_id: AnswerRunId,
        *,
        generation_id: IndexGenerationId,
        plan_hash: str,
        answer_hash: str,
        citation_manifest_hash: str,
        expected_revision: int,
    ) -> AnswerRunRecord:
        if await self._session.get(IndexGenerationTable, generation_id) is None:
            raise invariant("answer run generation", run_id)
        return await self._finish(
            run_id,
            AnswerRunStatus.ANSWERED,
            expected_revision=expected_revision,
            generation_id=generation_id,
            plan_hash=plan_hash,
            answer_hash=answer_hash,
            citation_manifest_hash=citation_manifest_hash,
            refusal_code=None,
        )

    async def refuse(
        self,
        run_id: AnswerRunId,
        *,
        reason_code: str,
        expected_revision: int,
        generation_id: IndexGenerationId | None = None,
        plan_hash: str | None = None,
    ) -> AnswerRunRecord:
        if not reason_code.strip():
            raise ValueError("answer refusal reason must not be empty")
        return await self._finish(
            run_id,
            AnswerRunStatus.REFUSED,
            expected_revision=expected_revision,
            generation_id=generation_id,
            plan_hash=plan_hash,
            refusal_code=reason_code,
            answer_hash=None,
            citation_manifest_hash=None,
        )

    async def fail(
        self,
        run_id: AnswerRunId,
        *,
        reason_code: str,
        expected_revision: int,
    ) -> AnswerRunRecord:
        if not reason_code.strip():
            raise ValueError("answer failure reason must not be empty")
        return await self._finish(
            run_id,
            AnswerRunStatus.FAILED,
            expected_revision=expected_revision,
            generation_id=None,
            plan_hash=None,
            refusal_code=reason_code,
            answer_hash=None,
            citation_manifest_hash=None,
        )

    async def _finish(
        self,
        run_id: AnswerRunId,
        target: AnswerRunStatus,
        *,
        expected_revision: int,
        generation_id: IndexGenerationId | None,
        plan_hash: str | None,
        refusal_code: str | None,
        answer_hash: str | None,
        citation_manifest_hash: str | None,
    ) -> AnswerRunRecord:
        row = await self._required(run_id)
        if row.revision != expected_revision:
            raise concurrent("answer run", run_id)
        require_transition(row.status, target)
        try:
            result = await self._session.execute(
                update(AnswerRunTable)
                .where(
                    AnswerRunTable.id == run_id,
                    AnswerRunTable.revision == expected_revision,
                )
                .values(
                    generation_id=generation_id,
                    plan_hash=plan_hash,
                    status=target,
                    refusal_code=refusal_code,
                    answer_hash=answer_hash,
                    citation_manifest_hash=citation_manifest_hash,
                    completed_at=utc_now(),
                    revision=expected_revision + 1,
                    updated_at=utc_now(),
                )
                .returning(AnswerRunTable)
            )
        except IntegrityError as error:
            raise_constraint_error(error, entity="answer run", identifier=run_id)
        except OperationalError as error:
            raise_operational_error(error)
        updated = result.scalar_one_or_none()
        if updated is None:
            exists = await self._session.get(AnswerRunTable, run_id)
            if exists is None:
                raise not_found("answer run", run_id)
            raise concurrent("answer run", run_id)
        return to_record(AnswerRunRecord, updated)

    async def _required(self, run_id: AnswerRunId) -> AnswerRunTable:
        row = await self._session.get(AnswerRunTable, run_id)
        if row is None:
            raise not_found("answer run", run_id)
        return row


__all__ = ["AnswerRepository"]
