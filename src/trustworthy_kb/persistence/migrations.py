"""Alembic schema-version checks used by application startup."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from trustworthy_kb.persistence.errors import DatabaseSchemaMismatchError

_PROJECT_ROOT = Path(__file__).parents[3]
_MIGRATION_COMMAND = "uv run alembic upgrade head"


async def assert_schema_current(engine: AsyncEngine, config: Config | None = None) -> None:
    """Raise a safe error unless the connected database is at the Alembic head."""

    alembic_config = config or Config(str(_PROJECT_ROOT / "alembic.ini"))
    expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()

    async with engine.connect() as connection:
        current_revision = await connection.run_sync(_current_revision)

    if current_revision != expected_head:
        raise DatabaseSchemaMismatchError(
            "database schema is not current; run " + _MIGRATION_COMMAND
        )


def _current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


__all__ = ["assert_schema_current"]
