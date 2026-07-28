"""Safe command-line entry point for one configured ingestion run."""

from __future__ import annotations

import asyncio
import sys

from pydantic import ValidationError

from trustworthy_kb.config import DatabaseSettings, IngestionSettings
from trustworthy_kb.ingestion.errors import IngestionError
from trustworthy_kb.ingestion.runner import IngestionReport, build_manual_runner
from trustworthy_kb.ingestion.workflow import run_ingestion_workflow
from trustworthy_kb.persistence import (
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)
from trustworthy_kb.persistence.errors import PersistenceError
from trustworthy_kb.persistence.migrations import assert_schema_current


async def _run_configured_ingestion() -> IngestionReport:
    ingestion_settings = IngestionSettings()
    database_settings = DatabaseSettings()
    engine = create_database_engine(database_settings)
    try:
        await assert_schema_current(engine)
        unit_of_work_factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
        runner = build_manual_runner(ingestion_settings, unit_of_work_factory)
        return await run_ingestion_workflow(
            runner,
            ingestion_settings.checkpoint_path_value,
        )
    finally:
        await engine.dispose()


def main() -> None:
    """Run one full scan and emit only a checkpoint-safe summary."""

    try:
        report = asyncio.run(_run_configured_ingestion())
    except Exception as error:
        print(_safe_error_message(error), file=sys.stderr)
        raise SystemExit(1) from None
    print(report.model_dump_json())


def _safe_error_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "ingestion configuration is invalid"
    if isinstance(error, (IngestionError, PersistenceError)):
        return str(error)
    return "ingestion failed"


__all__ = ["main"]
