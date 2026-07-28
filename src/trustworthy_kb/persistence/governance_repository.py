"""Async repository for governance execution and human-review state."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from trustworthy_kb.domain import (
    ActorType,
    GovernanceItemId,
    GovernanceItemRecord,
    GovernanceItemStage,
    GovernanceRunId,
    GovernanceRunRecord,
    GovernanceRunStatus,
    KnowledgeChangeId,
    QualityCheckId,
    ReviewRequestId,
    ReviewRequestRecord,
    ReviewRequestStatus,
    require_transition,
)
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.persistence.governance_tables import (
    GovernanceItemTable,
    GovernanceRunTable,
    ReviewRequestTable,
)
from trustworthy_kb.persistence.knowledge_tables import ClaimTable, QualityCheckTable
from trustworthy_kb.persistence.publication_tables import KnowledgeChangeTable
from trustworthy_kb.persistence.repository_base import (
    concurrent,
    flush_safely,
    invariant,
    not_found,
    to_record,
)

_TERMINAL_RUN_STATUSES = frozenset(
    {
        GovernanceRunStatus.COMPLETED,
        GovernanceRunStatus.PARTIAL_FAILED,
        GovernanceRunStatus.FAILED,
        GovernanceRunStatus.QUARANTINED,
    }
)


class GovernanceRepository:
    """Persist resumable L3 execution state through intent-specific methods."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_run(self, record: GovernanceRunRecord) -> GovernanceRunRecord:
        if record.revision != 1 or record.status is not GovernanceRunStatus.PLANNING:
            raise invariant("governance run creation", record.id)
        change = await self._session.get(KnowledgeChangeTable, record.knowledge_change_id)
        if change is None or change.target_version_id != record.target_source_version_id:
            raise invariant("governance run change", record.id)
        row = GovernanceRunTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="governance run", identifier=record.id)
        return to_record(GovernanceRunRecord, row)

    async def get_run(self, run_id: GovernanceRunId) -> GovernanceRunRecord:
        return to_record(GovernanceRunRecord, await self._run_row(run_id))

    async def get_run_for_change(
        self, change_id: KnowledgeChangeId, policy_version: str
    ) -> GovernanceRunRecord | None:
        row = await self._session.scalar(
            select(GovernanceRunTable).where(
                GovernanceRunTable.knowledge_change_id == change_id,
                GovernanceRunTable.policy_version == policy_version,
            )
        )
        return None if row is None else to_record(GovernanceRunRecord, row)

    async def transition_run(
        self,
        run_id: GovernanceRunId,
        target_status: GovernanceRunStatus,
        *,
        expected_revision: int,
        error_category: str | None = None,
    ) -> GovernanceRunRecord:
        row = await self._run_row(run_id)
        if row.revision != expected_revision:
            raise concurrent("governance run", run_id)
        require_transition(row.status, target_status)
        completed_at = utc_now() if target_status in _TERMINAL_RUN_STATUSES else None
        result = await self._session.execute(
            update(GovernanceRunTable)
            .where(
                GovernanceRunTable.id == run_id,
                GovernanceRunTable.revision == expected_revision,
            )
            .values(
                status=target_status,
                error_category=error_category,
                completed_at=completed_at,
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(GovernanceRunTable)
        )
        updated = result.scalar_one_or_none()
        if updated is None:
            raise concurrent("governance run", run_id)
        return to_record(GovernanceRunRecord, updated)

    async def set_run_counts(
        self,
        run_id: GovernanceRunId,
        *,
        total: int,
        decided: int,
        review: int,
        failed: int,
        quarantined: int,
        expected_revision: int,
    ) -> GovernanceRunRecord:
        values = (total, decided, review, failed, quarantined)
        if min(values) < 0 or sum(values[1:]) > total:
            raise invariant("governance run counts", run_id)
        result = await self._session.execute(
            update(GovernanceRunTable)
            .where(
                GovernanceRunTable.id == run_id,
                GovernanceRunTable.revision == expected_revision,
            )
            .values(
                total_items=total,
                decided_items=decided,
                review_items=review,
                failed_items=failed,
                quarantined_items=quarantined,
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(GovernanceRunTable)
        )
        updated = result.scalar_one_or_none()
        if updated is None:
            await self._run_row(run_id)
            raise concurrent("governance run", run_id)
        return to_record(GovernanceRunRecord, updated)

    async def add_item(self, record: GovernanceItemRecord) -> GovernanceItemRecord:
        if (
            record.revision != 1
            or record.attempt != 1
            or record.stage is not GovernanceItemStage.EXTRACTED
        ):
            raise invariant("governance item creation", record.id)
        await self._run_row(record.run_id)
        if await self._session.get(ClaimTable, record.claim_id) is None:
            raise invariant("governance item claim", record.id)
        row = GovernanceItemTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="governance item", identifier=record.id)
        return to_record(GovernanceItemRecord, row)

    async def list_items(self, run_id: GovernanceRunId) -> Sequence[GovernanceItemRecord]:
        rows = (
            await self._session.scalars(
                select(GovernanceItemTable)
                .where(GovernanceItemTable.run_id == run_id)
                .order_by(GovernanceItemTable.created_at, GovernanceItemTable.id)
            )
        ).all()
        return tuple(to_record(GovernanceItemRecord, row) for row in rows)

    async def get_item(self, item_id: GovernanceItemId) -> GovernanceItemRecord:
        return to_record(GovernanceItemRecord, await self._item_row(item_id))

    async def transition_item(
        self,
        item_id: GovernanceItemId,
        target_stage: GovernanceItemStage,
        *,
        expected_revision: int,
        error_category: str | None = None,
    ) -> GovernanceItemRecord:
        row = await self._item_row(item_id)
        if row.revision != expected_revision:
            raise concurrent("governance item", item_id)
        require_transition(row.stage, target_stage)
        result = await self._session.execute(
            update(GovernanceItemTable)
            .where(
                GovernanceItemTable.id == item_id,
                GovernanceItemTable.revision == expected_revision,
            )
            .values(
                stage=target_stage,
                error_category=error_category,
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(GovernanceItemTable)
        )
        updated = result.scalar_one_or_none()
        if updated is None:
            raise concurrent("governance item", item_id)
        return to_record(GovernanceItemRecord, updated)

    async def set_item_artifacts(
        self,
        item_id: GovernanceItemId,
        *,
        search_manifest_hash: str | None,
        evidence_pack_hash: str | None,
        expected_revision: int,
    ) -> GovernanceItemRecord:
        result = await self._session.execute(
            update(GovernanceItemTable)
            .where(
                GovernanceItemTable.id == item_id,
                GovernanceItemTable.revision == expected_revision,
            )
            .values(
                search_manifest_hash=search_manifest_hash,
                evidence_pack_hash=evidence_pack_hash,
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(GovernanceItemTable)
        )
        updated = result.scalar_one_or_none()
        if updated is None:
            await self._item_row(item_id)
            raise concurrent("governance item", item_id)
        return to_record(GovernanceItemRecord, updated)

    async def set_item_quality_check(
        self,
        item_id: GovernanceItemId,
        quality_check_id: QualityCheckId,
        *,
        expected_revision: int,
    ) -> GovernanceItemRecord:
        row = await self._item_row(item_id)
        if row.revision != expected_revision:
            raise concurrent("governance item", item_id)
        quality = await self._session.get(QualityCheckTable, quality_check_id)
        if quality is None or quality.claim_id != row.claim_id:
            raise invariant("governance item quality check", item_id)
        result = await self._session.execute(
            update(GovernanceItemTable)
            .where(
                GovernanceItemTable.id == item_id,
                GovernanceItemTable.revision == expected_revision,
            )
            .values(
                current_quality_check_id=quality_check_id,
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(GovernanceItemTable)
        )
        updated = result.scalar_one_or_none()
        if updated is None:
            raise concurrent("governance item", item_id)
        return to_record(GovernanceItemRecord, updated)

    async def add_review_request(self, record: ReviewRequestRecord) -> ReviewRequestRecord:
        if record.revision != 1 or record.status is not ReviewRequestStatus.PENDING:
            raise invariant("review request creation", record.id)
        quality = await self._session.get(QualityCheckTable, record.quality_check_id)
        change = await self._session.get(KnowledgeChangeTable, record.knowledge_change_id)
        if quality is None or quality.claim_id != record.claim_id or change is None:
            raise invariant("review request references", record.id)
        row = ReviewRequestTable(**record.model_dump(mode="python"))
        self._session.add(row)
        await flush_safely(self._session, entity="review request", identifier=record.id)
        return to_record(ReviewRequestRecord, row)

    async def list_pending_reviews(self) -> Sequence[ReviewRequestRecord]:
        rows = (
            await self._session.scalars(
                select(ReviewRequestTable)
                .where(ReviewRequestTable.status == ReviewRequestStatus.PENDING)
                .order_by(ReviewRequestTable.created_at, ReviewRequestTable.id)
            )
        ).all()
        return tuple(to_record(ReviewRequestRecord, row) for row in rows)

    async def get_review_request(self, request_id: ReviewRequestId) -> ReviewRequestRecord:
        return to_record(ReviewRequestRecord, await self._review_row(request_id))

    async def decide_review(
        self,
        request_id: ReviewRequestId,
        target_status: ReviewRequestStatus,
        *,
        decision_reason_code: str,
        actor_type: ActorType,
        expected_revision: int,
    ) -> ReviewRequestRecord:
        row = await self._review_row(request_id)
        if row.revision != expected_revision:
            raise concurrent("review request", request_id)
        require_transition(row.status, target_status)
        reason = decision_reason_code.strip()
        if not reason:
            raise invariant("review decision", request_id)
        result = await self._session.execute(
            update(ReviewRequestTable)
            .where(
                ReviewRequestTable.id == request_id,
                ReviewRequestTable.revision == expected_revision,
            )
            .values(
                status=target_status,
                decision_reason_code=reason,
                decision_actor_type=actor_type,
                decided_at=utc_now(),
                revision=expected_revision + 1,
                updated_at=utc_now(),
            )
            .returning(ReviewRequestTable)
        )
        updated = result.scalar_one_or_none()
        if updated is None:
            raise concurrent("review request", request_id)
        return to_record(ReviewRequestRecord, updated)

    async def _run_row(self, run_id: GovernanceRunId) -> GovernanceRunTable:
        row = await self._session.get(GovernanceRunTable, run_id)
        if row is None:
            raise not_found("governance run", run_id)
        return row

    async def _item_row(self, item_id: GovernanceItemId) -> GovernanceItemTable:
        row = await self._session.get(GovernanceItemTable, item_id)
        if row is None:
            raise not_found("governance item", item_id)
        return row

    async def _review_row(self, request_id: ReviewRequestId) -> ReviewRequestTable:
        row = await self._session.get(ReviewRequestTable, request_id)
        if row is None:
            raise not_found("review request", request_id)
        return row


__all__ = ["GovernanceRepository"]
