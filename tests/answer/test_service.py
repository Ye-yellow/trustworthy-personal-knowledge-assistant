from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trustworthy_kb.answer import (
    AnswerDraft,
    AnswerEvidence,
    AnswerRequest,
    AnswerSnapshotStore,
    AnswerStatus,
    CitationSupportDecision,
    CitationVerificationOutput,
    DraftAnswerClaim,
    PlannedScope,
    QueryPlan,
    QueryScope,
    RefusalCode,
    TrustedAnswerService,
)
from trustworthy_kb.config import AnswerSettings, DatabaseSettings
from trustworthy_kb.domain import (
    ClaimId,
    ClaimStatus,
    CuratedVersionId,
    IndexGenerationId,
    IndexGenerationRecord,
    IndexGenerationStatus,
    KnowledgeNoteId,
    Sensitivity,
    SourceVersionId,
)
from trustworthy_kb.persistence import (
    Base,
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)
from trustworthy_kb.publication.contracts import (
    KnowledgeChunk,
    RetrievalHit,
    RetrievalMode,
    RetrievalResult,
)


class Planner:
    def __init__(self, plan: QueryPlan) -> None:
        self.plan_value = plan
        self.calls = 0

    async def plan(self, _request: AnswerRequest) -> QueryPlan:
        self.calls += 1
        return self.plan_value


class Retriever:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls = 0

    async def retrieve(self, *_: Any, **__: Any) -> RetrievalResult:
        self.calls += 1
        return self.result


class Resolver:
    def __init__(self, evidence: tuple[AnswerEvidence, ...]) -> None:
        self.evidence = evidence

    async def resolve(self, _result: RetrievalResult) -> tuple[AnswerEvidence, ...]:
        return self.evidence


class Generator:
    def __init__(self, draft: AnswerDraft) -> None:
        self.draft = draft
        self.calls = 0

    async def generate(self, *_: Any) -> AnswerDraft:
        self.calls += 1
        return self.draft


class Verifier:
    def __init__(self, output: CitationVerificationOutput) -> None:
        self.output = output

    async def verify(self, *_: Any) -> CitationVerificationOutput:
        return self.output


def _evidence(generation_id: IndexGenerationId) -> AnswerEvidence:
    return AnswerEvidence(
        chunk_id="1" * 64,
        text="Python 3.12 supports the synthetic feature.",
        claim_ids=(ClaimId.generate(),),
        quality_status=ClaimStatus.VERIFIED,
        sensitivity=Sensitivity.PRIVATE,
        note_id=KnowledgeNoteId.generate(),
        curated_version_id=CuratedVersionId.generate(),
        generation_id=generation_id,
        vault_path="40-Concepts/Python.md",
        heading_path=("Python",),
        source_version_ids=(SourceVersionId.generate(),),
    )


def _retrieval(evidence: AnswerEvidence, generation_number: int = 1) -> RetrievalResult:
    chunk = KnowledgeChunk(
        chunk_id=evidence.chunk_id,
        note_id=evidence.note_id,
        curated_version_id=evidence.curated_version_id,
        claim_ids=evidence.claim_ids,
        text=evidence.text,
        heading_path=evidence.heading_path,
        ordinal=0,
        quality_status=evidence.quality_status,
        sensitivity=evidence.sensitivity,
        generation_id=evidence.generation_id,
        generation_number=generation_number,
        embedding_model="synthetic/embedding",
        chunker_version="markdown-v1",
        content_hash="2" * 64,
    )
    return RetrievalResult(
        hits=(RetrievalHit(chunk=chunk, retrieval_score=1.0, rerank_score=1.0),),
        mode=RetrievalMode.HYBRID,
        degraded=False,
        generation_id=evidence.generation_id,
    )


async def _factory(tmp_path: Path) -> tuple[Any, SqliteUnitOfWorkFactory, IndexGenerationRecord]:
    database_path = tmp_path / "service.db"
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    now = datetime.now(UTC)
    async with factory() as unit_of_work:
        staging = await unit_of_work.publication.add_index_generation(
            IndexGenerationRecord(
                id=IndexGenerationId.generate(),
                generation_number=1,
                embedding_model="synthetic/embedding",
                chunker_version="markdown-v1",
                collection_name="trustworthy_kb_chunks_g1",
                embedding_dimension=32,
                schema_version="milvus-hybrid-v1",
                manifest_hash="3" * 64,
                status=IndexGenerationStatus.STAGING,
                revision=1,
                created_at=now,
            )
        )
        active = await unit_of_work.publication.transition_index_generation(
            staging.id,
            IndexGenerationStatus.ACTIVE,
            expected_revision=staging.revision,
        )
        await unit_of_work.commit()
    return engine, factory, active


async def test_trusted_answer_service_verifies_before_output_and_replays_idempotently(
    tmp_path: Path,
) -> None:
    engine, factory, generation = await _factory(tmp_path)
    evidence = _evidence(generation.id)
    planner = Planner(
        QueryPlan(
            normalized_query="Python 3.12 synthetic feature",
            scope=PlannedScope.GENERAL,
            target_version="3.12",
        )
    )
    retriever = Retriever(_retrieval(evidence))
    draft = AnswerDraft(
        claims=(
            DraftAnswerClaim(
                statement="Python 3.12 supports the synthetic feature.",
                citation_chunk_ids=(evidence.chunk_id,),
            ),
        )
    )
    generator = Generator(draft)
    verifier = Verifier(
        CitationVerificationOutput(
            decisions=(
                CitationSupportDecision(
                    claim_index=0,
                    supported=True,
                    supporting_chunk_ids=(evidence.chunk_id,),
                    reason_code="SUPPORTED",
                ),
            )
        )
    )
    service = TrustedAnswerService(
        unit_of_work_factory=factory,
        planner=planner,
        retriever=retriever,
        evidence_resolver=Resolver((evidence,)),
        generator=generator,
        verifier=verifier,
        snapshots=AnswerSnapshotStore(tmp_path / "answer-snapshots"),
        settings=AnswerSettings(snapshot_root=str(tmp_path / "answer-snapshots")),
        model_name="sub2api/synthetic-model",
    )
    request = AnswerRequest(
        question="Does Python 3.12 support the private synthetic feature?",
        scope=QueryScope.GENERAL,
        software_version="3.12",
        operation_id="answer:service-success",
    )
    try:
        events = [event async for event in service.stream(request)]
        replay = await service.answer(request)

        assert [item.event.value for item in events] == [
            "accepted",
            "planned",
            "retrieved",
            "verified",
            "answer",
        ]
        assert all("Python 3.12 supports" not in event.model_dump_json() for event in events[:-1])
        assert events[-1].result is not None
        assert events[-1].result.status is AnswerStatus.ANSWERED
        assert replay == events[-1].result
        assert planner.calls == retriever.calls == generator.calls == 1
    finally:
        await engine.dispose()

    raw = (tmp_path / "service.db").read_bytes()
    assert request.question.encode() not in raw
    assert draft.claims[0].statement.encode() not in raw


async def test_trusted_answer_service_refuses_without_evidence(tmp_path: Path) -> None:
    engine, factory, generation = await _factory(tmp_path)
    planner = Planner(QueryPlan(normalized_query="unknown", scope=PlannedScope.GENERAL))
    retriever = Retriever(
        RetrievalResult(
            hits=(),
            mode=RetrievalMode.HYBRID,
            degraded=False,
            generation_id=generation.id,
        )
    )
    service = TrustedAnswerService(
        unit_of_work_factory=factory,
        planner=planner,
        retriever=retriever,
        evidence_resolver=Resolver(()),
        generator=Generator(
            AnswerDraft(
                claims=(DraftAnswerClaim(statement="unused", citation_chunk_ids=("4" * 64,)),)
            )
        ),
        verifier=Verifier(CitationVerificationOutput(decisions=())),
        snapshots=AnswerSnapshotStore(tmp_path / "snapshots"),
        settings=AnswerSettings(snapshot_root=str(tmp_path / "snapshots")),
        model_name="sub2api/synthetic-model",
    )
    try:
        result = await service.answer(
            AnswerRequest(question="Unknown synthetic fact?", operation_id="answer:no-evidence")
        )

        assert result.status is AnswerStatus.REFUSED
        assert result.reason_code is RefusalCode.NO_TRUSTED_EVIDENCE
    finally:
        await engine.dispose()
