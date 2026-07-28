from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.persistence import create_database_engine, create_session_factory


@pytest.mark.asyncio
async def test_engine_creates_parent_and_applies_sqlite_pragmas(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "control.db"
    settings = DatabaseSettings(
        url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        busy_timeout_ms=4321,
    )
    engine = create_database_engine(settings)
    try:
        async with engine.connect() as connection:
            foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
            journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
            synchronous = await connection.scalar(text("PRAGMA synchronous"))
            busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))

        assert database_path.exists()
        assert foreign_keys == 1
        assert journal_mode == "wal"
        assert synchronous == 1
        assert busy_timeout == 4321
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_factory_uses_non_expiring_sessions(tmp_path: Path) -> None:
    settings = DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'session.db').as_posix()}")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            assert session.sync_session.expire_on_commit is False
            assert await session.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()
