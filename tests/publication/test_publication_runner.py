from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.answer import EvaluationMetrics
from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    ChangeType,
    ClaimId,
    ClaimOriginRecord,
    ClaimRecord,
    ClaimStatus,
    ClaimType,
    ContentBlockId,
    ContentBlockRecord,
    IndexGenerationId,
    IndexGenerationRecord,
    IndexGenerationStatus,
    IndexJobStatus,
    KnowledgeChangeId,
    KnowledgeChangeRecord,
    KnowledgeChangeStatus,
    PublicationRunStatus,
    QualityCheckId,
    QualityCheckRecord,
    QualityVerdict,
    Sensitivity,
    SourceId,
    SourceRecord,
    SourceType,
    SourceVersionId,
    SourceVersionRecord,
    SourceVersionStatus,
    TrustTier,
)
from trustworthy_kb.persistence import (
    Base,
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)
from trustworthy_kb.publication.adapters import (
    DeterministicHashEmbedding,
    InMemoryVectorIndex,
)
from trustworthy_kb.publication.chunking import MarkdownChunker
from trustworthy_kb.publication.curation import (
    CuratedMarkdownRenderer,
    DeterministicCurationPlanner,
)
from trustworthy_kb.publication.generation_lifecycle import (
    GenerationLifecycleAction,
    GenerationLifecycleService,
    GenerationPromotionGate,
)
from trustworthy_kb.publication.indexing import GenerationIndexer
from trustworthy_kb.publication.lifecycle import NoteLifecycleAction, NoteLifecycleService
from trustworthy_kb.publication.runner import PublicationRunner
from trustworthy_kb.publication.snapshot_store import PublicationSnapshotStore
from trustworthy_kb.publication.vault import AtomicVaultPublisher


async def test_publication_runner_completes_and_replays_idempotently(tmp_path: Path) -> None:
    engine, factory, change_id, generation_id = await _seed(tmp_path)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    runner = PublicationRunner(
        unit_of_work_factory=factory,
        planner=DeterministicCurationPlanner(),
        renderer=CuratedMarkdownRenderer(),
        chunker=MarkdownChunker(),
        vault=AtomicVaultPublisher(vault_root),
        indexer=GenerationIndexer(DeterministicHashEmbedding(), InMemoryVectorIndex()),
        snapshots=PublicationSnapshotStore(tmp_path / "publication-snapshots"),
        model_name="deterministic/test",
        prompt_version="curation-v1",
        quality_policy_version="test-v1",
    )
    try:
        first = await runner.publish(
            change_id=change_id,
            generation_id=generation_id,
            final_relative_path="40-Concepts/Trusted.md",
            operation_id="publish-test-1",
        )
        replay = await runner.publish(
            change_id=change_id,
            generation_id=generation_id,
            final_relative_path="40-Concepts/Trusted.md",
            operation_id="publish-test-1",
        )

        assert first.status is PublicationRunStatus.COMPLETED
        assert replay == first
        assert first.chunk_count == 1
        assert (vault_root / first.vault_path).is_file()
        async with factory() as unit_of_work:
            note = await unit_of_work.publication.get_note(first.note_id)
            versions = await unit_of_work.publication.list_curated_versions(first.note_id)
            notes = await unit_of_work.publication.list_active_notes(generation_id)
            generations = await unit_of_work.publication.list_index_generations()
            job = await unit_of_work.publication.find_index_job("publish-test-1")
        assert note.current_curated_version_id == first.curated_version_id
        assert note.active_index_generation_id == generation_id
        assert len(versions) == 1
        assert notes == (note,)
        assert generations[0].id == generation_id
        assert job is not None
    finally:
        await engine.dispose()


async def test_note_lifecycle_delete_and_restore_converge_across_all_stores(
    tmp_path: Path,
) -> None:
    engine, factory, change_id, generation_id = await _seed(tmp_path)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    index = InMemoryVectorIndex()
    chunker = MarkdownChunker()
    indexer = GenerationIndexer(DeterministicHashEmbedding(), index)
    snapshots = PublicationSnapshotStore(tmp_path / "publication-snapshots")
    vault = AtomicVaultPublisher(vault_root)
    runner = PublicationRunner(
        unit_of_work_factory=factory,
        planner=DeterministicCurationPlanner(),
        renderer=CuratedMarkdownRenderer(),
        chunker=chunker,
        vault=vault,
        indexer=indexer,
        snapshots=snapshots,
        model_name="deterministic/test",
        prompt_version="curation-v1",
        quality_policy_version="test-v1",
    )
    lifecycle = NoteLifecycleService(
        unit_of_work_factory=factory,
        snapshots=snapshots,
        chunker=chunker,
        indexer=indexer,
        vault=vault,
    )
    try:
        publication = await runner.publish(
            change_id=change_id,
            generation_id=generation_id,
            final_relative_path="40-Concepts/Lifecycle.md",
            operation_id="publish-lifecycle-1",
        )
        deleted = await lifecycle.delete(
            publication.note_id,
            operation_id="delete-lifecycle-1",
        )
        replayed_delete = await lifecycle.delete(
            publication.note_id,
            operation_id="delete-lifecycle-1",
        )
        assert deleted.action is NoteLifecycleAction.DELETE
        assert deleted.index_status is IndexJobStatus.DELETED
        assert replayed_delete.index_status is IndexJobStatus.DELETED
        assert not (vault_root / publication.vault_path).exists()
        assert (
            vault_root
            / "_AI"
            / "Trash"
            / str(publication.note_id)
            / f"{publication.curated_version_id}.md"
        ).is_file()
        async with factory() as unit_of_work:
            assert (
                await unit_of_work.publication.resolve_current_versions((publication.note_id,))
                == {}
            )

        restored = await lifecycle.restore(
            publication.note_id,
            operation_id="restore-lifecycle-1",
        )
        replayed_restore = await lifecycle.restore(
            publication.note_id,
            operation_id="restore-lifecycle-1",
        )
        assert restored.action is NoteLifecycleAction.RESTORE
        assert restored.index_status is IndexJobStatus.ACTIVE_INDEXED
        assert replayed_restore.index_status is IndexJobStatus.ACTIVE_INDEXED
        assert (vault_root / publication.vault_path).is_file()
        async with factory() as unit_of_work:
            assert await unit_of_work.publication.resolve_current_versions(
                (publication.note_id,)
            ) == {publication.note_id: (publication.curated_version_id, generation_id)}
            latest = await unit_of_work.audit.get_latest_operation_log("restore-lifecycle-1")
        assert latest is not None
        assert latest.action == "KNOWLEDGE_NOTE_RESTORED"
    finally:
        await engine.dispose()


async def test_generation_rebuild_promotion_and_rollback_switch_all_notes_atomically(
    tmp_path: Path,
) -> None:
    engine, factory, change_id, first_generation_id = await _seed(tmp_path)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    index = InMemoryVectorIndex()
    chunker = MarkdownChunker()
    indexer = GenerationIndexer(DeterministicHashEmbedding(), index)
    snapshots = PublicationSnapshotStore(tmp_path / "publication-snapshots")
    runner = PublicationRunner(
        unit_of_work_factory=factory,
        planner=DeterministicCurationPlanner(),
        renderer=CuratedMarkdownRenderer(),
        chunker=chunker,
        vault=AtomicVaultPublisher(vault_root),
        indexer=indexer,
        snapshots=snapshots,
        model_name="deterministic/test",
        prompt_version="curation-v1",
        quality_policy_version="test-v1",
    )
    generation_service = GenerationLifecycleService(
        unit_of_work_factory=factory,
        snapshots=snapshots,
        chunker=chunker,
        indexer=indexer,
        index=index,
    )
    try:
        publication = await runner.publish(
            change_id=change_id,
            generation_id=first_generation_id,
            final_relative_path="40-Concepts/Migration.md",
            operation_id="publish-migration-1",
        )
        second = await _add_generation(factory, generation_number=2)
        gate = GenerationPromotionGate(
            mode="deterministic",
            generation_id=second.id,
            passed=True,
            metrics=EvaluationMetrics(
                citation_precision=0.95,
                retrieval_recall=0.90,
                refusal_accuracy=1.0,
                unsafe_citation_count=0,
                case_count=1,
            ),
        )

        rebuilt = await generation_service.rebuild(
            second.id,
            operation_id="generation-rebuild-1",
        )
        replayed_rebuild = await generation_service.rebuild(
            second.id,
            operation_id="generation-rebuild-1",
        )
        assert rebuilt.action is GenerationLifecycleAction.REBUILD
        assert replayed_rebuild == rebuilt
        assert rebuilt.active_generation_id == first_generation_id
        assert rebuilt.target_status is IndexGenerationStatus.STAGING

        promoted = await generation_service.promote(
            second.id,
            gate=gate,
            operation_id="generation-promote-1",
        )
        replayed_promotion = await generation_service.promote(
            second.id,
            gate=gate,
            operation_id="generation-promote-1",
        )
        assert promoted.action is GenerationLifecycleAction.PROMOTE
        assert replayed_promotion == promoted
        assert promoted.active_generation_id == second.id
        assert promoted.note_count == 1
        async with factory() as unit_of_work:
            assert (await unit_of_work.publication.get_active_index_generation()).id == second.id  # type: ignore[union-attr]
            assert await unit_of_work.publication.resolve_current_versions(
                (publication.note_id,)
            ) == {publication.note_id: (publication.curated_version_id, second.id)}
            first = await unit_of_work.publication.get_index_generation(first_generation_id)
        assert first.status is IndexGenerationStatus.SUPERSEDED

        rolled_back = await generation_service.rollback(
            first_generation_id,
            operation_id="generation-rollback-1",
        )
        replayed_rollback = await generation_service.rollback(
            first_generation_id,
            operation_id="generation-rollback-1",
        )
        assert rolled_back.action is GenerationLifecycleAction.ROLLBACK
        assert replayed_rollback == rolled_back
        assert rolled_back.active_generation_id == first_generation_id
        async with factory() as unit_of_work:
            assert await unit_of_work.publication.resolve_current_versions(
                (publication.note_id,)
            ) == {
                publication.note_id: (
                    publication.curated_version_id,
                    first_generation_id,
                )
            }
    finally:
        await engine.dispose()


class _FailingDocumentEmbedding(DeterministicHashEmbedding):
    async def embed_documents(self, texts):
        del texts
        raise RuntimeError("synthetic model upgrade failure")


def test_generation_promotion_gate_loads_bounded_generation_bound_report(
    tmp_path: Path,
) -> None:
    gate = GenerationPromotionGate(
        mode="deterministic",
        generation_id=IndexGenerationId.generate(),
        passed=True,
        metrics=EvaluationMetrics(
            citation_precision=1.0,
            retrieval_recall=1.0,
            refusal_accuracy=1.0,
            unsafe_citation_count=0,
            case_count=1,
        ),
    )
    path = tmp_path / "gate.json"
    path.write_text(gate.model_dump_json(), encoding="utf-8")

    assert GenerationPromotionGate.load(path) == gate


async def test_failed_generation_upgrade_keeps_old_active_and_can_be_aborted(
    tmp_path: Path,
) -> None:
    engine, factory, change_id, first_generation_id = await _seed(tmp_path)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    index = InMemoryVectorIndex()
    snapshots = PublicationSnapshotStore(tmp_path / "publication-snapshots")
    chunker = MarkdownChunker()
    await PublicationRunner(
        unit_of_work_factory=factory,
        planner=DeterministicCurationPlanner(),
        renderer=CuratedMarkdownRenderer(),
        chunker=chunker,
        vault=AtomicVaultPublisher(vault_root),
        indexer=GenerationIndexer(DeterministicHashEmbedding(), index),
        snapshots=snapshots,
        model_name="deterministic/test",
        prompt_version="curation-v1",
        quality_policy_version="test-v1",
    ).publish(
        change_id=change_id,
        generation_id=first_generation_id,
        final_relative_path="40-Concepts/UpgradeFailure.md",
        operation_id="publish-upgrade-failure-1",
    )
    second = await _add_generation(factory, generation_number=2)
    service = GenerationLifecycleService(
        unit_of_work_factory=factory,
        snapshots=snapshots,
        chunker=chunker,
        indexer=GenerationIndexer(_FailingDocumentEmbedding(), index),
        index=index,
    )
    try:
        with pytest.raises(RuntimeError, match="document embedding"):
            await service.rebuild(
                second.id,
                operation_id="generation-failure-1",
            )
        async with factory() as unit_of_work:
            active = await unit_of_work.publication.get_active_index_generation()
            staging = await unit_of_work.publication.get_index_generation(second.id)
        assert active is not None and active.id == first_generation_id
        assert staging.status is IndexGenerationStatus.STAGING

        aborted = await service.abort(second.id, operation_id="generation-abort-1")
        assert aborted.action is GenerationLifecycleAction.ABORT
        assert aborted.active_generation_id == first_generation_id
        assert aborted.target_status is IndexGenerationStatus.FAILED
    finally:
        await engine.dispose()


class _FlakyIndexer:
    def __init__(self, delegate: GenerationIndexer) -> None:
        self._delegate = delegate
        self.calls = 0

    async def index(self, chunks: object) -> int:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("synthetic index outage")
        return await self._delegate.index(chunks)  # type: ignore[arg-type]


async def test_publication_runner_resumes_failed_indexing_without_half_publish(
    tmp_path: Path,
) -> None:
    engine, factory, change_id, generation_id = await _seed(tmp_path)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    index = InMemoryVectorIndex()
    flaky = _FlakyIndexer(GenerationIndexer(DeterministicHashEmbedding(), index))
    runner = PublicationRunner(
        unit_of_work_factory=factory,
        planner=DeterministicCurationPlanner(),
        renderer=CuratedMarkdownRenderer(),
        chunker=MarkdownChunker(),
        vault=AtomicVaultPublisher(vault_root),
        indexer=flaky,  # type: ignore[arg-type]
        snapshots=PublicationSnapshotStore(tmp_path / "publication-snapshots"),
        model_name="deterministic/test",
        prompt_version="curation-v1",
        quality_policy_version="test-v1",
    )
    try:
        with pytest.raises(RuntimeError, match="synthetic index outage"):
            await runner.publish(
                change_id=change_id,
                generation_id=generation_id,
                final_relative_path="40-Concepts/Recovery.md",
                operation_id="publish-recovery-1",
            )
        assert not (vault_root / "40-Concepts" / "Recovery.md").exists()

        report = await runner.publish(
            change_id=change_id,
            generation_id=generation_id,
            final_relative_path="40-Concepts/Recovery.md",
            operation_id="publish-recovery-1",
        )
        assert report.status is PublicationRunStatus.COMPLETED
        assert flaky.calls == 2
    finally:
        await engine.dispose()


async def _seed(
    tmp_path: Path,
) -> tuple[object, SqliteUnitOfWorkFactory, KnowledgeChangeId, IndexGenerationId]:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'publication.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    now = datetime.now(UTC)
    source_id = SourceId.generate()
    source_version_id = SourceVersionId.generate()
    block_id = ContentBlockId.generate()
    claim_id = ClaimId.generate()
    change_id = KnowledgeChangeId.generate()
    generation_id = IndexGenerationId.generate()
    async with factory() as unit_of_work:
        await unit_of_work.sources.add_source(
            SourceRecord(
                id=source_id,
                source_type=SourceType.USER_INPUT,
                canonical_uri="user://publication-test",
                owner="test",
                trust_tier=TrustTier.T0,
                sensitivity=Sensitivity.PRIVATE,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        await unit_of_work.sources.append_source_version(
            SourceVersionRecord(
                id=source_version_id,
                source_id=source_id,
                version_number=1,
                content_hash="1" * 64,
                byte_size=1,
                media_type="text/plain",
                captured_at=now,
                original_path="publication.txt",
                status=SourceVersionStatus.CAPTURED,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        await unit_of_work.sources.add_content_blocks(
            (
                ContentBlockRecord(
                    id=block_id,
                    source_version_id=source_version_id,
                    ordinal=0,
                    block_type="paragraph",
                    anchor="publication",
                    text_hash="2" * 64,
                    character_count=1,
                    created_at=now,
                ),
            )
        )
        claim = await unit_of_work.knowledge.add_claim(
            ClaimRecord(
                id=claim_id,
                claim_fingerprint=hashlib.sha256(b"publication claim").hexdigest(),
                claim_family_key="3" * 64,
                claim_type=ClaimType.FACT,
                subject="Assistant",
                predicate="uses",
                object_json={"value": "verified evidence"},
                scope_json={},
                sensitivity=Sensitivity.PRIVATE,
                status=ClaimStatus.PROPOSED,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        await unit_of_work.knowledge.attach_claim_origin(
            ClaimOriginRecord(
                claim_id=claim.id,
                content_block_id=block_id,
                origin_span_json={"start": 0, "end": 1},
                created_at=now,
            )
        )
        quality = await unit_of_work.knowledge.record_quality_check(
            QualityCheckRecord(
                id=QualityCheckId.generate(),
                claim_id=claim.id,
                policy_version="test-v1",
                verdict=QualityVerdict.VERIFIED,
                dimensions_json={"synthetic": 1},
                reason_code="TEST_VERIFIED",
                reason_summary="synthetic verified claim",
                evidence_snapshot_hash="4" * 64,
                created_at=now,
            ),
            (),
        )
        claim = await unit_of_work.knowledge.set_current_quality_check(
            claim.id, quality.id, expected_revision=claim.revision
        )
        await unit_of_work.knowledge.transition_claim(
            claim.id, ClaimStatus.VERIFIED, expected_revision=claim.revision
        )
        change = await unit_of_work.publication.add_knowledge_change(
            KnowledgeChangeRecord(
                id=change_id,
                source_id=source_id,
                target_version_id=source_version_id,
                change_type=ChangeType.CREATED,
                diff_hash="5" * 64,
                diff_summary_json={"kind": "synthetic"},
                status=KnowledgeChangeStatus.RECEIVED,
                operation_id="publication-seed",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        change = await unit_of_work.publication.transition_knowledge_change(
            change.id, KnowledgeChangeStatus.VALIDATING, expected_revision=change.revision
        )
        await unit_of_work.publication.transition_knowledge_change(
            change.id,
            KnowledgeChangeStatus.PUBLISH_INTENT,
            expected_revision=change.revision,
        )
        await unit_of_work.publication.add_index_generation(
            IndexGenerationRecord(
                id=generation_id,
                generation_number=1,
                embedding_model="test/hash-embedding",
                chunker_version="markdown-v1",
                collection_name="trustworthy_kb_chunks_g1",
                embedding_dimension=32,
                schema_version="milvus-hybrid-v1",
                manifest_hash="6" * 64,
                status=IndexGenerationStatus.STAGING,
                revision=1,
                created_at=now,
            )
        )
        await unit_of_work.commit()
    return engine, factory, change_id, generation_id


async def _add_generation(
    factory: SqliteUnitOfWorkFactory,
    *,
    generation_number: int,
) -> IndexGenerationRecord:
    now = datetime.now(UTC)
    record = IndexGenerationRecord(
        id=IndexGenerationId.generate(),
        generation_number=generation_number,
        embedding_model="test/hash-embedding",
        chunker_version="markdown-v1",
        collection_name=f"trustworthy_kb_chunks_g{generation_number}",
        embedding_dimension=32,
        schema_version="milvus-hybrid-v1",
        manifest_hash=f"{generation_number}" * 64,
        status=IndexGenerationStatus.STAGING,
        revision=1,
        created_at=now,
    )
    async with factory() as unit_of_work:
        created = await unit_of_work.publication.add_index_generation(record)
        await unit_of_work.commit()
    return created
