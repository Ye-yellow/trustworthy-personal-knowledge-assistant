from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from trustworthy_kb.answer import (
    AnswerCitationVerifier,
    AnswerPlanner,
    AnswerRequest,
    AnswerSnapshotStore,
    AnswerStatus,
    QueryScope,
    SqliteAnswerEvidenceResolver,
    StructuredAnswerGenerator,
    TrustedAnswerService,
)
from trustworthy_kb.config import AnswerSettings, DatabaseSettings, LLMSettings, RetrievalSettings
from trustworthy_kb.domain import (
    ChangeType,
    ClaimId,
    ClaimStatus,
    ClaimType,
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
    PublicationRunId,
    PublicationRunRecord,
    PublicationRunStatus,
    Sensitivity,
    SourceId,
    SourceRecord,
    SourceType,
    SourceVersionId,
    SourceVersionRecord,
    SourceVersionStatus,
    TrustTier,
)
from trustworthy_kb.governance.audit import AuditedModelGateway
from trustworthy_kb.governance.fingerprints import canonical_json_hash
from trustworthy_kb.llm import ModelGateway, ModelRouter
from trustworthy_kb.persistence import (
    Base,
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)
from trustworthy_kb.publication.adapters import (
    BgeM3Embedding,
    BgeReranker,
    MilvusVectorIndex,
    SqliteCurrentVersionResolver,
)
from trustworthy_kb.publication.chunking import MarkdownChunker
from trustworthy_kb.publication.contracts import CurationClaim, CurationGroup, CurationPlan
from trustworthy_kb.publication.curation import CuratedMarkdownRenderer
from trustworthy_kb.publication.indexing import GenerationIndexer
from trustworthy_kb.publication.retrieval import HybridRetriever
from trustworthy_kb.publication.snapshot_store import PublicationSnapshotStore

_MARKER = "TRUSTKB_P0_OK"


@pytest.mark.integration
async def test_real_p0_trusted_answer_closes_every_provider_boundary(tmp_path: Path) -> None:
    if os.environ.get("TRUSTKB_RUN_P0_INTEGRATION") != "1":
        pytest.skip("set TRUSTKB_RUN_P0_INTEGRATION=1 to run the real synthetic P0 closure")

    llm_settings = LLMSettings(_env_file=".env")
    retrieval_settings = RetrievalSettings(_env_file=".env")
    if llm_settings.provider != "sub2api" or llm_settings.api_key is None:
        pytest.fail("P0 integration requires the configured local sub2api credential")

    database_path = tmp_path / "p0.db"
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    prefix = f"trustkb_p0_{uuid4().hex[:12]}_g"
    index = MilvusVectorIndex(
        uri=retrieval_settings.milvus_uri,
        collection_prefix=prefix,
        consistency="Strong",
        timeout_seconds=60,
    )
    cache_root = retrieval_settings.model_cache_root_value
    embedding = BgeM3Embedding(
        model_name=retrieval_settings.embedding_model,
        dimension=retrieval_settings.embedding_dimension,
        device="cpu",
        batch_size=1,
        max_length=128,
        use_fp16=False,
        cache_dir=cache_root / "hub",
    )
    reranker = BgeReranker(
        model_name=retrieval_settings.reranker_model,
        device="cpu",
        batch_size=1,
        max_length=128,
        use_fp16=False,
        cache_dir=cache_root / "hub",
    )
    publication_snapshots = PublicationSnapshotStore(tmp_path / "publication-snapshots")
    answer_snapshots = AnswerSnapshotStore(tmp_path / "answer-snapshots")
    collection = index.collection_name(1)

    try:
        generation, expected_chunk_id = await _seed_active_publication(
            factory=factory,
            index=index,
            embedding=embedding,
            snapshots=publication_snapshots,
            collection_name=collection,
        )
        audited = AuditedModelGateway(
            ModelGateway(ModelRouter(llm_settings)),
            factory,
            llm_settings,
        )
        gateway = cast(ModelGateway, audited)
        answer_settings = AnswerSettings(
            snapshot_root=str(tmp_path / "answer-snapshots"),
            max_question_characters=1000,
        )
        service = TrustedAnswerService(
            unit_of_work_factory=factory,
            planner=AnswerPlanner(gateway, prompt_version=answer_settings.prompt_version),
            retriever=HybridRetriever(
                embedding=embedding,
                index=index,
                current_versions=SqliteCurrentVersionResolver(factory),
                reranker=reranker,
                allow_bm25_only=False,
                rrf_k=retrieval_settings.rrf_k,
            ),
            evidence_resolver=SqliteAnswerEvidenceResolver(factory, publication_snapshots),
            generator=StructuredAnswerGenerator(
                gateway,
                prompt_version=answer_settings.prompt_version,
                max_claims=answer_settings.max_answer_claims,
                max_claim_characters=answer_settings.max_claim_characters,
            ),
            verifier=AnswerCitationVerifier(
                gateway,
                prompt_version=answer_settings.prompt_version,
            ),
            snapshots=answer_snapshots,
            settings=answer_settings,
            model_name=f"{llm_settings.provider}/{llm_settings.answer_model or llm_settings.model}",
        )
        question = "What is the synthetic P0 acceptance marker in the trusted note?"
        events = [
            event
            async for event in service.stream(
                AnswerRequest(
                    question=question,
                    scope=QueryScope.GENERAL,
                    top_k=1,
                    operation_id="answer:p0-synthetic-closure",
                )
            )
        ]

        result = events[-1].result
        assert result is not None
        assert result.status is AnswerStatus.ANSWERED
        assert _MARKER in result.answer_markdown
        assert result.generation_id == generation.id
        assert [citation.chunk_id for citation in result.citations] == [expected_chunk_id]
        assert [event.event.value for event in events] == [
            "accepted",
            "planned",
            "retrieved",
            "verified",
            "answer",
        ]
        assert all(_MARKER not in event.model_dump_json() for event in events[:-1])
    finally:
        await index.close()
        await _drop_collection(retrieval_settings.milvus_uri, collection)
        await engine.dispose()

    database_bytes = database_path.read_bytes()
    assert _MARKER.encode() not in database_bytes
    assert b"What is the synthetic P0" not in database_bytes


async def _seed_active_publication(
    *,
    factory: SqliteUnitOfWorkFactory,
    index: MilvusVectorIndex,
    embedding: BgeM3Embedding,
    snapshots: PublicationSnapshotStore,
    collection_name: str,
) -> tuple[IndexGenerationRecord, str]:
    now = datetime.now(UTC).replace(microsecond=0)
    source_id = SourceId.generate()
    source_version_id = SourceVersionId.generate()
    change_id = KnowledgeChangeId.generate()
    note_id = KnowledgeNoteId.generate()
    curated_version_id = CuratedVersionId.generate()
    generation_id = IndexGenerationId.generate()
    claim = CurationClaim(
        id=ClaimId.generate(),
        claim_type=ClaimType.FACT,
        subject="Synthetic P0 acceptance marker",
        predicate="is",
        object_json={"value": _MARKER},
        status=ClaimStatus.VERIFIED,
        sensitivity=Sensitivity.PRIVATE,
    )
    artifact = CuratedMarkdownRenderer().render(
        note_id=note_id,
        curated_version_id=curated_version_id,
        based_on_change_id=change_id,
        version_number=1,
        plan=CurationPlan(
            title="Synthetic P0 Acceptance",
            groups=(CurationGroup(heading="Marker", claim_ids=(claim.id,)),),
        ),
        claims=(claim,),
        source_ids=(source_id,),
        source_version_ids=(source_version_id,),
        model_name="synthetic/deterministic",
        prompt_version="synthetic-v1",
        quality_policy_version="synthetic-v1",
        created_at=now,
    )
    chunks = MarkdownChunker().chunk(
        artifact,
        (claim,),
        generation_id=generation_id,
        generation_number=1,
        embedding_model=embedding.model_name,
    )
    await snapshots.put(artifact, (claim,))
    assert await GenerationIndexer(embedding, index).index(chunks) == len(chunks)

    async with factory() as unit_of_work:
        await unit_of_work.sources.add_source(
            SourceRecord(
                id=source_id,
                source_type=SourceType.USER_INPUT,
                canonical_uri="user://synthetic-p0-acceptance",
                owner="synthetic-test",
                trust_tier=TrustTier.T0,
                sensitivity=Sensitivity.PRIVATE,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        source_version = await unit_of_work.sources.append_source_version(
            SourceVersionRecord(
                id=source_version_id,
                source_id=source_id,
                version_number=1,
                content_hash="1" * 64,
                byte_size=1,
                media_type="text/markdown",
                captured_at=now,
                original_path="Inbox/Synthetic-P0.md",
                status=SourceVersionStatus.CAPTURED,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        source_version = await unit_of_work.sources.transition_source_version(
            source_version.id,
            SourceVersionStatus.PARSED,
            expected_revision=source_version.revision,
        )
        await unit_of_work.sources.transition_source_version(
            source_version.id,
            SourceVersionStatus.READY,
            expected_revision=source_version.revision,
        )
        change = await unit_of_work.publication.add_knowledge_change(
            KnowledgeChangeRecord(
                id=change_id,
                source_id=source_id,
                target_version_id=source_version_id,
                change_type=ChangeType.CREATED,
                diff_hash="2" * 64,
                diff_summary_json={"synthetic": True},
                status=KnowledgeChangeStatus.RECEIVED,
                operation_id="p0-synthetic-closure",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        change = await unit_of_work.publication.transition_knowledge_change(
            change.id,
            KnowledgeChangeStatus.VALIDATING,
            expected_revision=change.revision,
        )
        change = await unit_of_work.publication.transition_knowledge_change(
            change.id,
            KnowledgeChangeStatus.PUBLISH_INTENT,
            expected_revision=change.revision,
        )
        note = await unit_of_work.publication.add_note(
            KnowledgeNoteRecord(
                id=note_id,
                canonical_path="40-Concepts/Synthetic-P0.md",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        curated = await unit_of_work.publication.add_curated_version(
            CuratedVersionRecord(
                id=curated_version_id,
                note_id=note_id,
                version_number=1,
                based_on_change_id=change_id,
                content_hash=artifact.content_hash,
                vault_path=note.canonical_path,
                status=CuratedVersionStatus.DRAFT,
                staging_path="_AI/Staging/Synthetic-P0.md",
                claim_set_hash=canonical_json_hash([str(claim.id)]),
                operation_id="p0-synthetic-closure",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        curated = await unit_of_work.publication.transition_curated_version(
            curated.id,
            CuratedVersionStatus.VALIDATING,
            expected_revision=curated.revision,
        )
        curated = await unit_of_work.publication.transition_curated_version(
            curated.id,
            CuratedVersionStatus.STAGING,
            expected_revision=curated.revision,
        )
        generation = await unit_of_work.publication.add_index_generation(
            IndexGenerationRecord(
                id=generation_id,
                generation_number=1,
                embedding_model=embedding.model_name,
                chunker_version="markdown-v1",
                collection_name=collection_name,
                embedding_dimension=embedding.dimension,
                schema_version="milvus-hybrid-v1",
                manifest_hash="3" * 64,
                status=IndexGenerationStatus.STAGING,
                revision=1,
                created_at=now,
            )
        )
        job = await unit_of_work.publication.add_index_job(
            IndexJobRecord(
                id=IndexJobId.generate(),
                object_type=EntityType.CURATED_VERSION,
                object_id=curated.id,
                generation_id=generation.id,
                status=IndexJobStatus.PENDING,
                attempt=0,
                operation_id="p0-synthetic-closure",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        job = await unit_of_work.publication.transition_index_job(
            job.id,
            IndexJobStatus.INDEXING,
            expected_revision=job.revision,
        )
        job = await unit_of_work.publication.mark_index_job_indexed(
            job.id,
            content_hash=artifact.content_hash,
            indexed_chunk_count=len(chunks),
            expected_revision=job.revision,
        )
        run = await unit_of_work.publication.add_publication_run(
            PublicationRunRecord(
                id=PublicationRunId.generate(),
                knowledge_change_id=change.id,
                note_id=note.id,
                curated_version_id=curated.id,
                target_generation_id=generation.id,
                operation_id="p0-synthetic-closure",
                status=PublicationRunStatus.PLANNING,
                attempt=1,
                revision=1,
                started_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        for status in (
            PublicationRunStatus.CURATING,
            PublicationRunStatus.VAULT_STAGED,
            PublicationRunStatus.INDEXING,
            PublicationRunStatus.INDEX_VERIFIED,
            PublicationRunStatus.VAULT_PUBLISHED,
            PublicationRunStatus.ACTIVATING,
        ):
            run = await unit_of_work.publication.transition_publication_run(
                run.id,
                status,
                expected_revision=run.revision,
            )
        _, _, _, _, _, generation = await unit_of_work.publication.activate_publication(
            run_id=run.id,
            job_id=job.id,
            expected_run_revision=run.revision,
            expected_note_revision=note.revision,
            expected_version_revision=curated.revision,
            expected_job_revision=job.revision,
            expected_change_revision=change.revision,
            expected_generation_revision=generation.revision,
        )
        await unit_of_work.commit()
    return generation, chunks[0].chunk_id


async def _drop_collection(uri: str, collection_name: str) -> None:
    pymilvus = pytest.importorskip("pymilvus")
    client = pymilvus.MilvusClient(uri=uri, timeout=60)
    try:
        if client.has_collection(collection_name=collection_name, timeout=60):
            client.drop_collection(collection_name=collection_name, timeout=60)
    finally:
        client.close()
