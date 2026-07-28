from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    IngestionAction,
    IngestionItemId,
    IngestionItemRecord,
    IngestionItemStatus,
    IngestionRunId,
    IngestionRunRecord,
    IngestionRunStatus,
    Sensitivity,
    SourceId,
    SourceLocationRecord,
    SourceRecord,
    SourceType,
    TrustTier,
)
from trustworthy_kb.ingestion import IngestionAlreadyRunningError
from trustworthy_kb.persistence import Base, create_database_engine, create_session_factory
from trustworthy_kb.persistence.errors import ConcurrentModificationError, RecordNotFoundError
from trustworthy_kb.persistence.ingestion_repository import IngestionRepository
from trustworthy_kb.persistence.source_repository import SourceRepository


def now() -> datetime:
    return datetime.now(UTC)


def run_record(*, vault_hash: str = "a" * 64) -> IngestionRunRecord:
    timestamp = now()
    return IngestionRunRecord(
        id=IngestionRunId.generate(),
        vault_id_hash=vault_hash,
        scan_scope_hash="b" * 64,
        manifest_hash="c" * 64,
        status=IngestionRunStatus.PLANNING,
        revision=1,
        started_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )


def item_record(run_id: IngestionRunId, *, path_hash: str = "d" * 64) -> IngestionItemRecord:
    timestamp = now()
    return IngestionItemRecord(
        id=IngestionItemId.generate(),
        run_id=run_id,
        action=IngestionAction.CREATED,
        relative_path="Synthetic/note.md",
        path_key=path_hash,
        content_hash="e" * 64,
        status=IngestionItemStatus.PENDING,
        operation_id=f"operation-{path_hash[:8]}",
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


def source_record(source_id: SourceId | None = None, *, suffix: str = "one") -> SourceRecord:
    timestamp = now()
    return SourceRecord(
        id=source_id or SourceId.generate(),
        source_type=SourceType.OBSIDIAN_MARKDOWN,
        canonical_uri=f"obsidian://vault/synthetic/{suffix}",
        owner="synthetic-owner",
        trust_tier=TrustTier.T0,
        sensitivity=Sensitivity.PRIVATE,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


def location_record(source_id: SourceId, run_id: IngestionRunId) -> SourceLocationRecord:
    timestamp = now()
    return SourceLocationRecord(
        source_id=source_id,
        vault_id_hash="a" * 64,
        relative_path="Synthetic/note.md",
        path_key="d" * 64,
        file_key="f" * 64,
        last_seen_run_id=run_id,
        observed_size=12,
        observed_mtime_ns=100,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


async def repositories(
    tmp_path: Path,
) -> tuple[object, object, IngestionRepository, SourceRepository]:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'ingestion-repo.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    return engine, session, IngestionRepository(session), SourceRepository(session)


@pytest.mark.asyncio
async def test_ingestion_repository_run_and_item_success_lifecycle(tmp_path: Path) -> None:
    engine, session, repository, _sources = await repositories(tmp_path)
    run = run_record()
    item = item_record(run.id)
    try:
        assert await repository.begin_run(run) == run
        planned, saved = await repository.save_plan(
            run.id,
            "1" * 64,
            [item],
            expected_run_revision=1,
        )
        assert planned.manifest_hash == "1" * 64
        assert saved == (item,)
        applying_run = await repository.transition_run(
            run.id,
            IngestionRunStatus.APPLYING,
            expected_revision=planned.revision,
        )
        assert await repository.list_pending_items(run.id) == (item,)
        applying_item = await repository.start_item(item.id, expected_revision=1)
        completed_item = await repository.complete_item(
            item.id,
            IngestionItemStatus.SUCCEEDED,
            expected_revision=applying_item.revision,
        )
        assert completed_item.completed_at is not None
        summary = await repository.summarize_run(run.id)
        assert summary.total == summary.succeeded == 1
        completed_run = await repository.transition_run(
            run.id,
            IngestionRunStatus.COMPLETED,
            expected_revision=applying_run.revision,
        )
        assert completed_run.succeeded_items == 1
        assert completed_run.completed_at is not None
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_repository_retry_and_partial_failure_lifecycle(tmp_path: Path) -> None:
    engine, session, repository, _sources = await repositories(tmp_path)
    run = run_record()
    item = item_record(run.id)
    try:
        await repository.begin_run(run)
        planned, _ = await repository.save_plan(run.id, "1" * 64, [item], expected_run_revision=1)
        applying_run = await repository.transition_run(
            run.id, IngestionRunStatus.APPLYING, expected_revision=planned.revision
        )
        applying_item = await repository.start_item(item.id, expected_revision=1)
        failed = await repository.fail_item(
            item.id,
            error_category="PARSE_FAILED",
            expected_revision=applying_item.revision,
        )
        retried = await repository.retry_item(
            item.id,
            operation_id="operation-retry-2",
            expected_revision=failed.revision,
        )
        assert retried.attempt == 2
        applying_retry = await repository.start_item(item.id, expected_revision=retried.revision)
        quarantined = await repository.complete_item(
            item.id,
            IngestionItemStatus.QUARANTINED,
            expected_revision=applying_retry.revision,
            safety_signals={"SECRET_MATERIAL": 1},
        )
        assert quarantined.safety_signals_json == {"SECRET_MATERIAL": 1}
        partial = await repository.transition_run(
            run.id,
            IngestionRunStatus.PARTIAL_FAILED,
            expected_revision=applying_run.revision,
        )
        assert partial.quarantined_items == 1
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_repository_guards_active_run_and_stale_revision(tmp_path: Path) -> None:
    engine, session, repository, _sources = await repositories(tmp_path)
    run = run_record()
    try:
        await repository.begin_run(run)
        with pytest.raises(IngestionAlreadyRunningError):
            await repository.begin_run(run_record())
        with pytest.raises(ConcurrentModificationError):
            await repository.save_plan(
                run.id,
                "1" * 64,
                [item_record(run.id)],
                expected_run_revision=99,
            )
        missing_id = IngestionItemId.generate()
        with pytest.raises(RecordNotFoundError) as captured:
            await repository.get_item(missing_id)
        assert str(missing_id) not in str(captured.value)
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_locations_move_touch_delete_and_allow_path_reuse(tmp_path: Path) -> None:
    engine, session, repository, sources = await repositories(tmp_path)
    run = run_record()
    first_source = source_record()
    second_source = source_record(suffix="two")
    try:
        await repository.begin_run(run)
        await sources.add_source(first_source)
        await sources.add_source(second_source)
        original = await repository.record_source_location(location_record(first_source.id, run.id))
        moved = await repository.move_source_location(
            first_source.id,
            relative_path="Synthetic/moved.md",
            path_key="2" * 64,
            file_key="3" * 64,
            last_seen_run_id=run.id,
            observed_size=15,
            observed_mtime_ns=200,
            expected_revision=original.revision,
        )
        touched = await repository.touch_source_location(
            first_source.id,
            file_key=moved.file_key,
            last_seen_run_id=run.id,
            observed_size=16,
            observed_mtime_ns=300,
            expected_revision=moved.revision,
        )
        with pytest.raises(ConcurrentModificationError):
            await repository.touch_source_location(
                first_source.id,
                file_key=None,
                last_seen_run_id=run.id,
                observed_size=1,
                observed_mtime_ns=1,
                expected_revision=1,
            )
        deleted = await repository.mark_source_location_deleted(
            first_source.id, expected_revision=touched.revision
        )
        assert deleted.deleted_at is not None
        replacement = location_record(second_source.id, run.id).model_copy(
            update={"path_key": moved.path_key, "relative_path": moved.relative_path}
        )
        await repository.record_source_location(replacement)
        live = await repository.list_live_source_locations(run.vault_id_hash)
        assert [location.source_id for location in live] == [second_source.id]
        assert (
            await repository.get_source_location(first_source.id, include_deleted=True)
        ).deleted_at is not None
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
