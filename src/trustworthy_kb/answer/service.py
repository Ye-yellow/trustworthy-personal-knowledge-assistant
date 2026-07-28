"""Fail-closed orchestration for trusted answers and progress events."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

from trustworthy_kb.answer.contracts import (
    AnsweredResult,
    AnswerEvent,
    AnswerEventType,
    AnswerRequest,
    AnswerResult,
    AnswerStatus,
    RefusalCode,
    RefusedResult,
)
from trustworthy_kb.answer.errors import AnswerIntegrityError
from trustworthy_kb.answer.planning import retrieval_query_for_plan
from trustworthy_kb.answer.ports import (
    AnswerEvidenceResolver,
    AnswerGenerationGateway,
    AnswerPlanningGateway,
    AnswerRetrievalGateway,
    AnswerVerificationGateway,
)
from trustworthy_kb.answer.rendering import render_verified_answer
from trustworthy_kb.answer.snapshot_store import AnswerSnapshotStore
from trustworthy_kb.answer.verification import (
    validate_citation_closed_set,
    validate_semantic_support,
)
from trustworthy_kb.config import AnswerSettings
from trustworthy_kb.domain import (
    AnswerRunId,
    AnswerRunRecord,
    AnswerRunStatus,
    AnswerScope,
    IndexGenerationId,
    IndexGenerationRecord,
    IndexGenerationStatus,
)
from trustworthy_kb.governance.fingerprints import canonical_json_hash
from trustworthy_kb.persistence import SqliteUnitOfWorkFactory

_REFUSAL_MESSAGES = {
    RefusalCode.NO_ACTIVE_GENERATION: "No active trusted knowledge index is available.",
    RefusalCode.NO_TRUSTED_EVIDENCE: "The trusted knowledge base does not contain enough evidence.",
    RefusalCode.EVIDENCE_NOT_LOCATABLE: (
        "Retrieved evidence could not be traced to an immutable source."
    ),
    RefusalCode.VERSION_MISMATCH: "The available evidence does not match the requested version.",
    RefusalCode.STALE_EVIDENCE: "Current evidence is unavailable or stale.",
    RefusalCode.RETRIEVAL_UNAVAILABLE: "Trusted retrieval is currently unavailable.",
    RefusalCode.PLANNING_FAILED: "The question could not be planned safely.",
    RefusalCode.GENERATION_FAILED: "A grounded answer could not be generated safely.",
    RefusalCode.CITATION_VALIDATION_FAILED: "The generated claims were not supported by citations.",
    RefusalCode.POLICY_BLOCKED: "The request was blocked by the trusted-answer policy.",
}


class TrustedAnswerService:
    """Answer from ACTIVE evidence only and never expose an unverified model draft."""

    def __init__(
        self,
        *,
        unit_of_work_factory: SqliteUnitOfWorkFactory,
        planner: AnswerPlanningGateway,
        retriever: AnswerRetrievalGateway,
        evidence_resolver: AnswerEvidenceResolver,
        generator: AnswerGenerationGateway,
        verifier: AnswerVerificationGateway,
        snapshots: AnswerSnapshotStore,
        settings: AnswerSettings,
        model_name: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = unit_of_work_factory
        self._planner = planner
        self._retriever = retriever
        self._evidence_resolver = evidence_resolver
        self._generator = generator
        self._verifier = verifier
        self._snapshots = snapshots
        self._settings = settings
        self._model_name = model_name.strip()
        self._clock = clock or (lambda: datetime.now(UTC))
        if not self._model_name:
            raise ValueError("answer model name must not be empty")

    async def answer(self, request: AnswerRequest) -> AnswerResult:
        """Return the single verified or refused terminal result."""

        terminal: AnswerResult | None = None
        async for event in self.stream(request):
            if event.result is not None:
                terminal = event.result
        if terminal is None:
            raise AnswerIntegrityError("answer execution ended without a terminal result")
        return terminal

    async def stream(self, request: AnswerRequest) -> AsyncIterator[AnswerEvent]:
        """Yield privacy-safe progress and one terminal event."""

        if len(request.question) > self._settings.max_question_characters:
            raise AnswerIntegrityError("question exceeds the configured character limit")
        run, replay = await self._begin(request)
        event_id = 1
        yield self._event(
            event_id,
            AnswerEventType.ACCEPTED,
            run.id,
            {"status": "accepted"},
        )
        event_id += 1
        if replay is not None:
            yield self._terminal_event(event_id, replay)
            return

        try:
            plan = await self._planner.plan(request)
        except Exception:
            result = await self._refuse(run, RefusalCode.PLANNING_FAILED)
            yield self._terminal_event(event_id, result)
            return
        plan_hash = canonical_json_hash(plan.model_dump(mode="json"))
        yield self._event(
            event_id,
            AnswerEventType.PLANNED,
            run.id,
            {
                "scope": plan.scope.value,
                "requires_current": plan.requires_current,
                "has_target_version": plan.target_version is not None,
            },
        )
        event_id += 1

        generation = await self._active_generation()
        if generation is None:
            result = await self._refuse(
                run,
                RefusalCode.NO_ACTIVE_GENERATION,
                plan_hash=plan_hash,
            )
            yield self._terminal_event(event_id, result)
            return
        try:
            retrieval = await self._retriever.retrieve(
                retrieval_query_for_plan(request, plan, at=self._clock()),
                generation_id=generation.id,
                generation_number=generation.generation_number,
            )
        except Exception:
            result = await self._refuse(
                run,
                RefusalCode.RETRIEVAL_UNAVAILABLE,
                generation_id=generation.id,
                plan_hash=plan_hash,
            )
            yield self._terminal_event(event_id, result)
            return
        yield self._event(
            event_id,
            AnswerEventType.RETRIEVED,
            run.id,
            {
                "hit_count": len(retrieval.hits),
                "mode": retrieval.mode.value,
                "degraded": retrieval.degraded,
            },
        )
        event_id += 1

        try:
            evidence = await self._evidence_resolver.resolve(retrieval)
        except Exception:
            result = await self._refuse(
                run,
                RefusalCode.EVIDENCE_NOT_LOCATABLE,
                generation_id=generation.id,
                plan_hash=plan_hash,
            )
            yield self._terminal_event(event_id, result)
            return
        if len(evidence) < self._settings.min_evidence_count:
            reason = (
                RefusalCode.STALE_EVIDENCE
                if plan.requires_current and retrieval.hits
                else RefusalCode.NO_TRUSTED_EVIDENCE
            )
            result = await self._refuse(
                run,
                reason,
                generation_id=generation.id,
                plan_hash=plan_hash,
            )
            yield self._terminal_event(event_id, result)
            return
        if plan.target_version and not any(
            plan.target_version.casefold() in item.text.casefold() for item in evidence
        ):
            result = await self._refuse(
                run,
                RefusalCode.VERSION_MISMATCH,
                generation_id=generation.id,
                plan_hash=plan_hash,
            )
            yield self._terminal_event(event_id, result)
            return

        try:
            draft = await self._generator.generate(plan, evidence)
        except Exception:
            result = await self._refuse(
                run,
                RefusalCode.GENERATION_FAILED,
                generation_id=generation.id,
                plan_hash=plan_hash,
            )
            yield self._terminal_event(event_id, result)
            return
        try:
            verification = await self._verifier.verify(draft, evidence)
            validate_citation_closed_set(
                draft,
                evidence,
                max_claims=self._settings.max_answer_claims,
                max_claim_characters=self._settings.max_claim_characters,
            )
            validate_semantic_support(draft, verification)
            answer_markdown, citations = render_verified_answer(draft, evidence)
        except Exception:
            result = await self._refuse(
                run,
                RefusalCode.CITATION_VALIDATION_FAILED,
                generation_id=generation.id,
                plan_hash=plan_hash,
            )
            yield self._terminal_event(event_id, result)
            return
        yield self._event(
            event_id,
            AnswerEventType.VERIFIED,
            run.id,
            {
                "claim_count": len(verification.decisions),
                "citation_count": len(citations),
            },
        )
        event_id += 1

        answered_result = AnsweredResult(
            run_id=run.id,
            answer_markdown=answer_markdown,
            claims=draft.claims,
            citations=citations,
            generation_id=generation.id,
            degraded=retrieval.degraded,
        )
        answer_hash = await self._snapshots.put(answered_result)
        citation_hash = canonical_json_hash([item.model_dump(mode="json") for item in citations])
        async with self._uow_factory() as unit_of_work:
            completed = await unit_of_work.answers.complete_answer(
                run.id,
                generation_id=generation.id,
                plan_hash=plan_hash,
                answer_hash=answer_hash,
                citation_manifest_hash=citation_hash,
                expected_revision=run.revision,
            )
            await unit_of_work.commit()
        if completed.status is not AnswerRunStatus.ANSWERED:
            raise AnswerIntegrityError("answer run did not reach the answered state")
        yield self._terminal_event(event_id, answered_result)

    async def _begin(self, request: AnswerRequest) -> tuple[AnswerRunRecord, AnswerResult | None]:
        question_hash = canonical_json_hash(
            request.model_dump(mode="json", exclude={"operation_id"})
        )
        generated_id = AnswerRunId.generate()
        operation_id = request.operation_id or f"answer:{question_hash}:{generated_id}"
        async with self._uow_factory() as unit_of_work:
            existing = await unit_of_work.answers.find_run(operation_id)
            if existing is not None:
                if existing.question_hash != question_hash:
                    raise AnswerIntegrityError("answer operation ID was reused for another request")
                return existing, await self._replay(existing)
            now = self._clock()
            created = await unit_of_work.answers.add_run(
                AnswerRunRecord(
                    id=generated_id,
                    operation_id=operation_id,
                    question_hash=question_hash,
                    scope=AnswerScope(request.scope.value),
                    status=AnswerRunStatus.IN_PROGRESS,
                    model_name=self._model_name,
                    prompt_version=self._settings.prompt_version,
                    revision=1,
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await unit_of_work.commit()
        return created, None

    async def _replay(self, run: AnswerRunRecord) -> AnswerResult | None:
        if run.status is AnswerRunStatus.IN_PROGRESS:
            return None
        if run.status is AnswerRunStatus.ANSWERED:
            if run.answer_hash is None:
                raise AnswerIntegrityError("answered run has no verified snapshot hash")
            result = await self._snapshots.get(run.answer_hash)
            if result.run_id != run.id or result.generation_id != run.generation_id:
                raise AnswerIntegrityError("verified answer snapshot lineage changed")
            return result
        if run.status is AnswerRunStatus.REFUSED:
            if run.refusal_code is None:
                raise AnswerIntegrityError("refused answer run has no reason")
            try:
                reason = RefusalCode(run.refusal_code)
            except ValueError:
                raise AnswerIntegrityError("answer run has an invalid refusal reason") from None
            return RefusedResult(
                run_id=run.id,
                reason_code=reason,
                message=_REFUSAL_MESSAGES[reason],
            )
        return RefusedResult(
            run_id=run.id,
            reason_code=RefusalCode.POLICY_BLOCKED,
            message=_REFUSAL_MESSAGES[RefusalCode.POLICY_BLOCKED],
        )

    async def _active_generation(self) -> IndexGenerationRecord | None:
        async with self._uow_factory() as unit_of_work:
            generation = await unit_of_work.publication.get_active_index_generation()
        if generation is None or generation.status is not IndexGenerationStatus.ACTIVE:
            return None
        return generation

    async def _refuse(
        self,
        run: AnswerRunRecord,
        reason: RefusalCode,
        *,
        generation_id: IndexGenerationId | None = None,
        plan_hash: str | None = None,
    ) -> RefusedResult:
        async with self._uow_factory() as unit_of_work:
            refused = await unit_of_work.answers.refuse(
                run.id,
                reason_code=reason.value,
                generation_id=generation_id,
                plan_hash=plan_hash,
                expected_revision=run.revision,
            )
            await unit_of_work.commit()
        return RefusedResult(
            run_id=refused.id,
            reason_code=reason,
            message=_REFUSAL_MESSAGES[reason],
        )

    def _event(
        self,
        event_id: int,
        event: AnswerEventType,
        run_id: AnswerRunId,
        payload: dict[str, str | int | bool],
    ) -> AnswerEvent:
        return AnswerEvent(
            event_id=event_id,
            event=event,
            run_id=run_id,
            occurred_at=self._clock(),
            payload=payload,
        )

    def _terminal_event(self, event_id: int, result: AnswerResult) -> AnswerEvent:
        event = (
            AnswerEventType.ANSWER
            if result.status is AnswerStatus.ANSWERED
            else AnswerEventType.REFUSAL
        )
        return AnswerEvent(
            event_id=event_id,
            event=event,
            run_id=result.run_id,
            occurred_at=self._clock(),
            payload={"status": result.status.value},
            result=result,
        )


__all__ = ["TrustedAnswerService"]
