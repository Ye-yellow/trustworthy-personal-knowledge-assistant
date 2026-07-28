from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    ChangeType,
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
from trustworthy_kb.persistence import Base, create_database_engine, create_session_factory
from trustworthy_kb.persistence.publication_repository import PublicationRepository
from trustworthy_kb.persistence.source_repository import SourceRepository


def _now() -> datetime:
    return datetime.now(UTC)


async def _source(repository: SourceRepository) -> tuple[SourceId, SourceVersionId]:
    now = _now()
    source = SourceRecord(
        id=SourceId.generate(),
        source_type=SourceType.USER_INPUT,
        canonical_uri="user://l4-saga",
        owner="test",
        trust_tier=TrustTier.T0,
        sensitivity=Sensitivity.PRIVATE,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    version = SourceVersionRecord(
        id=SourceVersionId.generate(),
        source_id=source.id,
        version_number=1,
        content_hash="1" * 64,
        byte_size=1,
        media_type="text/markdown",
        captured_at=now,
        original_path="Inbox/l4.md",
        status=SourceVersionStatus.CAPTURED,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    await repository.add_source(source)
    await repository.append_source_version(version)
    parsed = await repository.transition_source_version(
        version.id, SourceVersionStatus.PARSED, expected_revision=1
    )
    await repository.transition_source_version(
        version.id, SourceVersionStatus.READY, expected_revision=parsed.revision
    )
    return source.id, version.id


async def test_publication_saga_repository_activates_all_authoritative_pointers(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'saga.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    source_repository = SourceRepository(session)
    repository = PublicationRepository(session)
    try:
        source_id, source_version_id = await _source(source_repository)
        now = _now()
        change = await repository.add_knowledge_change(
            KnowledgeChangeRecord(
                id=KnowledgeChangeId.generate(),
                source_id=source_id,
                target_version_id=source_version_id,
                change_type=ChangeType.CREATED,
                diff_hash="2" * 64,
                diff_summary_json={"created": True},
                status=KnowledgeChangeStatus.RECEIVED,
                operation_id="l4-saga",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        change = await repository.transition_knowledge_change(
            change.id, KnowledgeChangeStatus.VALIDATING, expected_revision=change.revision
        )
        change = await repository.transition_knowledge_change(
            change.id,
            KnowledgeChangeStatus.PUBLISH_INTENT,
            expected_revision=change.revision,
        )
        note = await repository.add_note(
            KnowledgeNoteRecord(
                id=KnowledgeNoteId.generate(),
                canonical_path="40-Concepts/L4.md",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        version = await repository.add_curated_version(
            CuratedVersionRecord(
                id=CuratedVersionId.generate(),
                note_id=note.id,
                version_number=1,
                based_on_change_id=change.id,
                content_hash="3" * 64,
                vault_path=note.canonical_path,
                status=CuratedVersionStatus.DRAFT,
                staging_path="_AI/Staging/l4.md",
                claim_set_hash="4" * 64,
                operation_id="l4-saga",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        version = await repository.transition_curated_version(
            version.id, CuratedVersionStatus.VALIDATING, expected_revision=version.revision
        )
        version = await repository.transition_curated_version(
            version.id, CuratedVersionStatus.STAGING, expected_revision=version.revision
        )
        generation = await repository.add_index_generation(
            IndexGenerationRecord(
                id=IndexGenerationId.generate(),
                generation_number=1,
                embedding_model="test/hash-embedding",
                chunker_version="markdown-v1",
                collection_name="trustworthy_kb_chunks_g1",
                embedding_dimension=32,
                schema_version="milvus-hybrid-v1",
                manifest_hash="5" * 64,
                status=IndexGenerationStatus.STAGING,
                revision=1,
                created_at=now,
            )
        )
        job = await repository.add_index_job(
            IndexJobRecord(
                id=IndexJobId.generate(),
                object_type=EntityType.CURATED_VERSION,
                object_id=version.id,
                generation_id=generation.id,
                status=IndexJobStatus.PENDING,
                attempt=0,
                operation_id="l4-saga",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        job = await repository.transition_index_job(
            job.id, IndexJobStatus.INDEXING, expected_revision=job.revision
        )
        job = await repository.mark_index_job_indexed(
            job.id,
            content_hash=version.content_hash,
            indexed_chunk_count=2,
            expected_revision=job.revision,
        )
        run = await repository.add_publication_run(
            PublicationRunRecord(
                id=PublicationRunId.generate(),
                knowledge_change_id=change.id,
                note_id=note.id,
                curated_version_id=version.id,
                target_generation_id=generation.id,
                operation_id="l4-saga",
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
            run = await repository.transition_publication_run(
                run.id, status, expected_revision=run.revision
            )

        (
            completed,
            active_note,
            active_version,
            active_job,
            active_change,
            active_generation,
        ) = await repository.activate_publication(
            run_id=run.id,
            job_id=job.id,
            expected_run_revision=run.revision,
            expected_note_revision=note.revision,
            expected_version_revision=version.revision,
            expected_job_revision=job.revision,
            expected_change_revision=change.revision,
            expected_generation_revision=generation.revision,
        )

        assert completed.status is PublicationRunStatus.COMPLETED
        assert active_note.current_curated_version_id == version.id
        assert active_note.active_index_generation_id == generation.id
        assert active_version.status is CuratedVersionStatus.ACTIVE
        assert active_version.published_at is not None
        assert active_job.status is IndexJobStatus.ACTIVE_INDEXED
        assert active_change.status is KnowledgeChangeStatus.ACTIVE
        assert active_generation.status is IndexGenerationStatus.ACTIVE
        assert await repository.find_publication_run("l4-saga") == completed
        assert await repository.resolve_current_versions((note.id,)) == {
            note.id: (version.id, generation.id)
        }
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()
