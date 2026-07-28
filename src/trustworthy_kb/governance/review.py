"""Human-review application service for governance decisions."""

from __future__ import annotations

from trustworthy_kb.domain import (
    ActorType,
    KnowledgeChangeStatus,
    ReviewRequestId,
    ReviewRequestRecord,
    ReviewRequestStatus,
)
from trustworthy_kb.persistence import SqliteUnitOfWorkFactory


class ReviewService:
    def __init__(self, unit_of_work_factory: SqliteUnitOfWorkFactory) -> None:
        self._uow_factory = unit_of_work_factory

    async def list_pending(self) -> tuple[ReviewRequestRecord, ...]:
        async with self._uow_factory() as unit_of_work:
            return tuple(await unit_of_work.governance.list_pending_reviews())

    async def decide(
        self,
        request_id: ReviewRequestId,
        target: ReviewRequestStatus,
        *,
        reason_code: str,
    ) -> ReviewRequestRecord:
        if target is ReviewRequestStatus.PENDING:
            raise ValueError("review decision must be terminal")
        async with self._uow_factory() as unit_of_work:
            request = await unit_of_work.governance.get_review_request(request_id)
            decided = await unit_of_work.governance.decide_review(
                request.id,
                target,
                decision_reason_code=reason_code,
                actor_type=ActorType.USER,
                expected_revision=request.revision,
            )
            change = await unit_of_work.publication.get_knowledge_change(
                request.knowledge_change_id
            )
            if change.status is KnowledgeChangeStatus.REVIEW_REQUIRED:
                if target is ReviewRequestStatus.APPROVED:
                    pending = await unit_of_work.governance.list_pending_reviews()
                    same_change_pending = any(
                        item.knowledge_change_id == change.id and item.id != request.id
                        for item in pending
                    )
                    if not same_change_pending:
                        await unit_of_work.publication.transition_knowledge_change(
                            change.id,
                            KnowledgeChangeStatus.PUBLISH_INTENT,
                            expected_revision=change.revision,
                        )
                else:
                    await unit_of_work.publication.transition_knowledge_change(
                        change.id,
                        KnowledgeChangeStatus.FAILED,
                        expected_revision=change.revision,
                    )
            await unit_of_work.commit()
            return decided


__all__ = ["ReviewService"]
