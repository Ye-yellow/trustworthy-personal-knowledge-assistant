"""Async SQLite engine and session construction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trustworthy_kb.config import DatabaseSettings


def create_database_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Create an async engine with required SQLite safety PRAGMAs."""

    _ensure_parent_directory(settings.url_value)
    engine = create_async_engine(
        settings.url_value,
        echo=False,
        pool_pre_ping=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(f"PRAGMA busy_timeout={settings.busy_timeout_ms}")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create non-expiring sessions for explicit Unit of Work boundaries."""

    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def _ensure_parent_directory(url: str) -> None:
    database = make_url(url).database
    if database is None or database == ":memory:":
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


__all__ = ["create_database_engine", "create_session_factory"]
