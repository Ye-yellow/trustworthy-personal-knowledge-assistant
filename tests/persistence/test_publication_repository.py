from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

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
    InvalidStateTransitionError,
    InvariantViolationError,
    KnowledgeChangeId,
    KnowledgeChangeRecord,
    KnowledgeChangeStatus,
    KnowledgeNoteId,
    KnowledgeNoteRecord,
    LineageEdgeId,
    LineageEdgeRecord,
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
from trustworthy_kb.persistence.errors import ConcurrentModificationError
from trustworthy_kb.persistence.publication_repository import PublicationRepository
from trustworthy_kb.persistence.source_repository import SourceRepository


def now() -> datetime:
    return datetime.now(UTC)


async def add_source_version(
    repository: SourceRepository,
    *,
    uri: str,
) -> tuple[SourceId, SourceVersionId]:
    timestamp = now()
    source_id = SourceId.generate()
    version_id = SourceVersionId.generate()
    await repository.add_source(
        SourceRecord(
            id=source_id,
            source_type=SourceType.USER_INPUT,
            canonical_uri=uri,
            owner="test",
            trust_tier=TrustTier.T0,
            sensitivity=Sensitivity.PRIVATE,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    await repository.append_source_version(
        SourceVersionRecord(
            id=version_id,
            source_id=source_id,
            version_number=1,
            content_hash=f"{int(source_id[-1], 36):064x}",
            byte_size=1,
            media_type="text/plain",
            captured_at=timestamp,
            original_path=f"{uri.rsplit('/', 1)[-1]}.txt",
            status=SourceVersionStatus.READY,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    return source_id, version_id


@pytest.mark.asyncio
async def test_publication_repository_controls_change_curation_lineage_and_indexing(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'publication.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    source_repository = SourceRepository(session)
    repository = PublicationRepository(session)
    try:
        source_id, version_id = await add_source_version(source_repository, uri="user://publish")
        timestamp = now()
        change = KnowledgeChangeRecord(
            id=KnowledgeChangeId.generate(),
            source_id=source_id,
            target_version_id=version_id,
            change_type=ChangeType.CREATED,
            diff_hash="7" * 64,
            diff_summary_json={"kind": "created"},
            status=KnowledgeChangeStatus.RECEIVED,
            operation_id="publish-op",
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        note = KnowledgeNoteRecord(
            id=KnowledgeNoteId.generate(),
            canonical_path="Knowledge/publish.md",
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        curated = CuratedVersionRecord(
            id=CuratedVersionId.generate(),
            note_id=note.id,
            version_number=1,
            based_on_change_id=change.id,
            content_hash="8" * 64,
            vault_path=note.canonical_path,
            status=CuratedVersionStatus.DRAFT,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        assert await repository.add_knowledge_change(change) == change
        validating_change = await repository.transition_knowledge_change(
            change.id,
            KnowledgeChangeStatus.VALIDATING,
            expected_revision=1,
        )
        assert validating_change.status is KnowledgeChangeStatus.VALIDATING
        assert await repository.add_note(note) == note
        assert await repository.add_curated_version(curated) == curated
        validating = await repository.transition_curated_version(
            curated.id,
            CuratedVersionStatus.VALIDATING,
            expected_revision=1,
        )
        staging = await repository.transition_curated_version(
            curated.id,
            CuratedVersionStatus.STAGING,
            expected_revision=validating.revision,
        )
        activated_note, active_version = await repository.activate_curated_version(
            note.id,
            curated.id,
            expected_note_revision=1,
            expected_version_revision=staging.revision,
        )
        assert activated_note.current_curated_version_id == curated.id
        assert active_version.status is CuratedVersionStatus.ACTIVE

        lineage = LineageEdgeRecord(
            id=LineageEdgeId.generate(),
            from_type=EntityType.SOURCE_VERSION,
            from_id=version_id,
            to_type=EntityType.CURATED_VERSION,
            to_id=curated.id,
            relation="curated_from",
            operation_id="publish-op",
            created_at=now(),
        )
        assert await repository.add_lineage_edge(lineage) == lineage

        generation = IndexGenerationRecord(
            id=IndexGenerationId.generate(),
            generation_number=1,
            embedding_model="embedding-v1",
            chunker_version="chunker-v1",
            status=IndexGenerationStatus.STAGING,
            revision=1,
            created_at=now(),
        )
        assert await repository.add_index_generation(generation) == generation
        active_generation = await repository.transition_index_generation(
            generation.id,
            IndexGenerationStatus.ACTIVE,
            expected_revision=1,
        )
        assert active_generation.activated_at is not None

        job = IndexJobRecord(
            id=IndexJobId.generate(),
            object_type=EntityType.CURATED_VERSION,
            object_id=curated.id,
            generation_id=generation.id,
            status=IndexJobStatus.PENDING,
            attempt=0,
            revision=1,
            created_at=now(),
            updated_at=now(),
        )
        assert await repository.add_index_job(job) == job
        indexing = await repository.transition_index_job(
            job.id,
            IndexJobStatus.INDEXING,
            expected_revision=1,
        )
        assert indexing.status is IndexJobStatus.INDEXING
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_publication_repository_enforces_cross_record_invariants_and_cas(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        DatabaseSettings(
            url=f"sqlite+aiosqlite:///{(tmp_path / 'publication-errors.db').as_posix()}"
        )
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    source_repository = SourceRepository(session)
    repository = PublicationRepository(session)
    try:
        first_source, _first_version = await add_source_version(
            source_repository, uri="user://first"
        )
        _second_source, second_version = await add_source_version(
            source_repository, uri="user://second"
        )
        timestamp = now()
        invalid_change = KnowledgeChangeRecord(
            id=KnowledgeChangeId.generate(),
            source_id=first_source,
            target_version_id=second_version,
            change_type=ChangeType.UPDATED,
            diff_hash="9" * 64,
            diff_summary_json={},
            status=KnowledgeChangeStatus.RECEIVED,
            operation_id="invalid-op",
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with pytest.raises(InvariantViolationError):
            await repository.add_knowledge_change(invalid_change)

        note = KnowledgeNoteRecord(
            id=KnowledgeNoteId.generate(),
            canonical_path="Knowledge/error.md",
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        await repository.add_note(note)
        with pytest.raises(ConcurrentModificationError):
            await repository.activate_curated_version(
                note.id,
                CuratedVersionId.generate(),
                expected_note_revision=99,
                expected_version_revision=1,
            )

        with pytest.raises(InvariantViolationError):
            await repository.add_lineage_edge(
                LineageEdgeRecord(
                    id=LineageEdgeId.generate(),
                    from_type=EntityType.SOURCE,
                    from_id=SourceId.generate(),
                    to_type=EntityType.SOURCE_VERSION,
                    to_id=second_version,
                    relation="missing_from",
                    operation_id="invalid-op",
                    created_at=now(),
                )
            )

        generation = IndexGenerationRecord(
            id=IndexGenerationId.generate(),
            generation_number=1,
            embedding_model="embedding-v1",
            chunker_version="chunker-v1",
            status=IndexGenerationStatus.ACTIVE,
            revision=1,
            created_at=now(),
            activated_at=now(),
        )
        await repository.add_index_generation(generation)
        with pytest.raises(InvalidStateTransitionError):
            await repository.transition_index_generation(
                generation.id,
                IndexGenerationStatus.STAGING,
                expected_revision=1,
            )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
