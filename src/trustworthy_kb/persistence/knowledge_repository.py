"""Async repository for claims, evidence, and immutable quality decisions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from trustworthy_kb.domain import (
    ClaimId,
    ClaimOriginRecord,
    ClaimRecord,
    ClaimStatus,
    EvidenceFamilyRecord,
    EvidenceRecord,
    QualityCheckEvidenceRecord,
    QualityCheckId,
    QualityCheckRecord,
    require_transition,
)
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.persistence.knowledge_tables import (
    ClaimOriginTable,
    ClaimTable,
    EvidenceFamilyTable,
    EvidenceTable,
    QualityCheckEvidenceTable,
    QualityCheckTable,
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


class KnowledgeRepository:
    """Persist governed knowledge records through intent-specific methods."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_claim(self, record: ClaimRecord) -> ClaimRecord:
        row = ClaimTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="claim", identifier=record.id)
        return to_record(ClaimRecord, row)

    async def attach_claim_origin(self, record: ClaimOriginRecord) -> ClaimOriginRecord:
        row = ClaimOriginTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="claim origin", identifier=record.claim_id)
        return to_record(ClaimOriginRecord, row)

    async def add_evidence_family(self, record: EvidenceFamilyRecord) -> EvidenceFamilyRecord:
        row = EvidenceFamilyTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="evidence family", identifier=record.id)
        return to_record(EvidenceFamilyRecord, row)

    async def add_evidence(self, record: EvidenceRecord) -> EvidenceRecord:
        await self._claim_row(record.claim_id)
        row = EvidenceTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="evidence", identifier=record.id)
        return to_record(EvidenceRecord, row)

    async def record_quality_check(
        self,
        record: QualityCheckRecord,
        evidence_snapshot: Sequence[QualityCheckEvidenceRecord],
    ) -> QualityCheckRecord:
        await self._claim_row(record.claim_id)
        for link in evidence_snapshot:
            if link.quality_check_id != record.id:
                raise invariant("quality check evidence", record.id)
            evidence = await self._session.get(EvidenceTable, link.evidence_id)
            if evidence is None or evidence.claim_id != record.claim_id:
                raise invariant("quality check evidence", record.id)

        row = QualityCheckTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="quality check", identifier=record.id)
        if evidence_snapshot:
            links = [
                QualityCheckEvidenceTable(**link.model_dump(mode="python"))
                for link in evidence_snapshot
            ]
            self._session.add_all(links)
            await flush_safely(
                self._session,
                entity="quality check evidence",
                identifier=record.id,
            )
        return to_record(QualityCheckRecord, row)

    async def transition_claim(
        self,
        claim_id: ClaimId,
        target_status: ClaimStatus,
        *,
        expected_revision: int,
    ) -> ClaimRecord:
        row = await self._claim_row(claim_id)
        require_transition(row.status, target_status)
        return await self._update_claim(
            claim_id,
            expected_revision,
            status=target_status,
        )

    async def set_current_quality_check(
        self,
        claim_id: ClaimId,
        quality_check_id: QualityCheckId,
        *,
        expected_revision: int,
    ) -> ClaimRecord:
        await self._claim_row(claim_id)
        quality_check = await self._session.get(QualityCheckTable, quality_check_id)
        if quality_check is None or quality_check.claim_id != claim_id:
            raise invariant("claim quality check", quality_check_id)
        return await self._update_claim(
            claim_id,
            expected_revision,
            current_quality_check_id=quality_check_id,
        )

    async def mark_claim_deleted(
        self,
        claim_id: ClaimId,
        *,
        expected_revision: int,
        deleted_at: datetime | None = None,
    ) -> ClaimRecord:
        await self._claim_row(claim_id)
        return await self._update_claim(
            claim_id,
            expected_revision,
            deleted_at=deleted_at or utc_now(),
        )

    async def _claim_row(self, claim_id: ClaimId) -> ClaimTable:
        row = await self._session.scalar(
            select(ClaimTable).where(ClaimTable.id == claim_id, ClaimTable.deleted_at.is_(None))
        )
        if row is None:
            raise not_found("claim", claim_id)
        return row

    async def _update_claim(
        self,
        claim_id: ClaimId,
        expected_revision: int,
        **values: object,
    ) -> ClaimRecord:
        statement = (
            update(ClaimTable)
            .where(
                ClaimTable.id == claim_id,
                ClaimTable.revision == expected_revision,
                ClaimTable.deleted_at.is_(None),
            )
            .values(**values, revision=expected_revision + 1, updated_at=utc_now())
            .returning(ClaimTable)
        )
        try:
            result = await self._session.execute(statement)
        except IntegrityError as error:
            raise_constraint_error(error, entity="claim", identifier=claim_id)
        except OperationalError as error:
            raise_operational_error(error)
        row = result.scalar_one_or_none()
        if row is not None:
            return to_record(ClaimRecord, row)
        exists = await self._session.scalar(select(ClaimTable.id).where(ClaimTable.id == claim_id))
        if exists is None:
            raise not_found("claim", claim_id)
        raise concurrent("claim", claim_id)


__all__ = ["KnowledgeRepository"]
