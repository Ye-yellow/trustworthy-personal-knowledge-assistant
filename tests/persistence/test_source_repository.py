from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    ContentBlockId,
    ContentBlockRecord,
    InvalidStateTransitionError,
    InvariantViolationError,
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
from trustworthy_kb.persistence.errors import (
    ConcurrentModificationError,
    DuplicateRecordError,
    RecordNotFoundError,
)
from trustworthy_kb.persistence.source_repository import SourceRepository


def now() -> datetime:
    return datetime.now(UTC)


def source_record(*, source_id: SourceId | None = None, uri: str = "user://source") -> SourceRecord:
    timestamp = now()
    return SourceRecord(
        id=source_id or SourceId.generate(),
        source_type=SourceType.USER_INPUT,
        canonical_uri=uri,
        owner="test-user",
        trust_tier=TrustTier.T0,
        sensitivity=Sensitivity.PRIVATE,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


def version_record(
    source_id: SourceId,
    *,
    version_id: SourceVersionId | None = None,
    version_number: int = 1,
    status: SourceVersionStatus = SourceVersionStatus.CAPTURED,
) -> SourceVersionRecord:
    timestamp = now()
    return SourceVersionRecord(
        id=version_id or SourceVersionId.generate(),
        source_id=source_id,
        version_number=version_number,
        content_hash=f"{version_number:064x}",
        byte_size=12,
        media_type="text/markdown",
        captured_at=timestamp,
        original_path=f"source-{version_number}.md",
        status=status,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


def block_record(version_id: SourceVersionId, *, ordinal: int = 0) -> ContentBlockRecord:
    return ContentBlockRecord(
        id=ContentBlockId.generate(),
        source_version_id=version_id,
        ordinal=ordinal,
        block_type="paragraph",
        anchor=f"block-{ordinal}",
        text_hash=f"{ordinal + 10:064x}",
        character_count=12,
        created_at=now(),
    )


async def repository_for(tmp_path: Path) -> tuple[object, object, SourceRepository]:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'source-repo.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    return engine, session, SourceRepository(session)


@pytest.mark.asyncio
async def test_source_repository_happy_path_and_soft_delete(tmp_path: Path) -> None:
    engine, session, repository = await repository_for(tmp_path)
    source = source_record()
    version = version_record(source.id)
    block = block_record(version.id)
    try:
        assert await repository.add_source(source) == source
        assert await repository.get_source(source.id) == source
        assert (
            await repository.find_source_by_identity(
                source.source_type, source.canonical_uri, source.owner
            )
            == source
        )
        assert await repository.append_source_version(version) == version
        assert await repository.add_content_blocks([block]) == (block,)

        parsed = await repository.transition_source_version(
            version.id,
            SourceVersionStatus.PARSED,
            expected_revision=1,
        )
        ready = await repository.transition_source_version(
            version.id,
            SourceVersionStatus.READY,
            expected_revision=parsed.revision,
        )
        activated = await repository.activate_source_version(
            source.id,
            version.id,
            expected_revision=1,
        )
        assert ready.status is SourceVersionStatus.READY
        assert activated.current_version_id == version.id
        assert activated.revision == 2

        deleted = await repository.mark_source_deleted(source.id, expected_revision=2)
        assert deleted.deleted_at is not None
        assert (
            await repository.find_source_by_identity(
                source.source_type, source.canonical_uri, source.owner
            )
            is None
        )
        with pytest.raises(RecordNotFoundError):
            await repository.get_source(source.id)
        assert (await repository.get_source(source.id, include_deleted=True)).id == source.id
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_repository_maps_duplicates_and_safe_lookup_errors(tmp_path: Path) -> None:
    engine, session, repository = await repository_for(tmp_path)
    source = source_record()
    try:
        await repository.add_source(source)
        duplicate = source_record(uri=source.canonical_uri)
        with pytest.raises(DuplicateRecordError) as captured:
            await repository.add_source(duplicate)
        assert source.canonical_uri not in str(captured.value)
        await session.rollback()

        missing_id = SourceId.generate()
        with pytest.raises(RecordNotFoundError) as missing:
            await repository.get_source(missing_id)
        assert str(missing_id) not in str(missing.value)
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_repository_rejects_invalid_transition_and_stale_revision(
    tmp_path: Path,
) -> None:
    engine, session, repository = await repository_for(tmp_path)
    source = source_record()
    version = version_record(source.id)
    try:
        await repository.add_source(source)
        await repository.append_source_version(version)
        parsed = await repository.transition_source_version(
            version.id,
            SourceVersionStatus.PARSED,
            expected_revision=1,
        )
        ready = await repository.transition_source_version(
            version.id,
            SourceVersionStatus.READY,
            expected_revision=parsed.revision,
        )
        with pytest.raises(InvalidStateTransitionError):
            await repository.transition_source_version(
                version.id,
                SourceVersionStatus.PARSED,
                expected_revision=ready.revision,
            )
        with pytest.raises(ConcurrentModificationError):
            await repository.activate_source_version(
                source.id,
                version.id,
                expected_revision=99,
            )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_repository_enforces_activation_invariants(tmp_path: Path) -> None:
    engine, session, repository = await repository_for(tmp_path)
    first_source = source_record(uri="user://first")
    second_source = source_record(uri="user://second")
    captured_version = version_record(first_source.id)
    other_version = version_record(second_source.id)
    try:
        await repository.add_source(first_source)
        await repository.add_source(second_source)
        await repository.append_source_version(captured_version)
        await repository.append_source_version(other_version)

        with pytest.raises(InvariantViolationError):
            await repository.activate_source_version(
                first_source.id,
                captured_version.id,
                expected_revision=1,
            )
        with pytest.raises(InvariantViolationError):
            await repository.activate_source_version(
                first_source.id,
                other_version.id,
                expected_revision=1,
            )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
