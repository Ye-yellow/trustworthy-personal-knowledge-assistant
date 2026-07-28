from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    Sensitivity,
    SourceId,
    SourceRecord,
    SourceType,
    TrustTier,
)
from trustworthy_kb.persistence import Base, create_database_engine, create_session_factory
from trustworthy_kb.persistence.errors import DatabaseBusyError, RecordNotFoundError
from trustworthy_kb.persistence.unit_of_work import SqliteUnitOfWorkFactory


def source_record(uri: str) -> SourceRecord:
    timestamp = datetime.now(UTC)
    return SourceRecord(
        id=SourceId.generate(),
        source_type=SourceType.USER_INPUT,
        canonical_uri=uri,
        owner="test",
        trust_tier=TrustTier.T0,
        sensitivity=Sensitivity.PRIVATE,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_unit_of_work_requires_explicit_commit(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'uow.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    committed = source_record("user://committed")
    rolled_back = source_record("user://rolled-back")
    try:
        async with factory() as unit_of_work:
            await unit_of_work.sources.add_source(committed)
            await unit_of_work.commit()

        async with factory() as unit_of_work:
            await unit_of_work.sources.add_source(rolled_back)

        async with factory() as unit_of_work:
            assert await unit_of_work.sources.get_source(committed.id) == committed
            with pytest.raises(RecordNotFoundError):
                await unit_of_work.sources.get_source(rolled_back.id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_and_preserves_original_exception(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'uow-error.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    source = source_record("user://exception")

    class ExpectedFailure(RuntimeError):
        pass

    failure = ExpectedFailure("preserve-me")
    try:
        with pytest.raises(ExpectedFailure) as captured:
            async with factory() as unit_of_work:
                await unit_of_work.sources.add_source(source)
                raise failure
        assert captured.value is failure

        async with factory() as unit_of_work:
            with pytest.raises(RecordNotFoundError):
                await unit_of_work.sources.get_source(source.id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_repository_maps_sqlite_write_lock_to_database_busy(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(
            url=f"sqlite+aiosqlite:///{(tmp_path / 'busy.db').as_posix()}",
            busy_timeout_ms=100,
        )
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    try:
        async with engine.connect() as locker:
            await locker.execute(text("BEGIN IMMEDIATE"))
            with pytest.raises(DatabaseBusyError):
                async with factory() as unit_of_work:
                    await unit_of_work.sources.add_source(source_record("user://busy"))
            await locker.rollback()
    finally:
        await engine.dispose()
