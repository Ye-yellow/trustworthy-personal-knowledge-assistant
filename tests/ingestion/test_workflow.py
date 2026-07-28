from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import func, select

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import IngestionRunId, IngestionRunStatus
from trustworthy_kb.ingestion import (
    ContentAddressedSnapshotStore,
    DocumentSafetyScanner,
    ManualIngestionRunner,
    MarkdownBlockParser,
    StableMarkdownReader,
    VaultFileObservation,
    VaultInventoryResult,
    file_key,
    path_key,
    run_ingestion_workflow,
)
from trustworthy_kb.ingestion.service import IngestionService
from trustworthy_kb.persistence import (
    Base,
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)
from trustworthy_kb.persistence.ingestion_tables import IngestionItemTable


class SingleFileInventory:
    def __init__(self, vault: Path) -> None:
        self._vault = vault

    async def inventory(self) -> VaultInventoryResult:
        note = self._vault / "note.md"
        stat = note.stat()
        return VaultInventoryResult(
            complete=True,
            files=(
                VaultFileObservation(
                    relative_path="note.md",
                    path_key=path_key("note.md"),
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    file_key=file_key(stat.st_dev, stat.st_ino),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_workflow_resumes_failed_apply_without_duplicate_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    private_body = "Synthetic checkpoint body"
    (vault / "note.md").write_text(f"# Synthetic\n\n{private_body}", encoding="utf-8")
    database_path = tmp_path / "control.db"
    checkpoint_path = tmp_path / "checkpoints" / "ingestion.sqlite"
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    vault_hash = "a" * 64
    runner = ManualIngestionRunner(
        inventory=SingleFileInventory(vault),
        unit_of_work_factory=factory,
        reader=StableMarkdownReader(vault, max_bytes=1024 * 1024, interval_ms=0),
        snapshots=ContentAddressedSnapshotStore(tmp_path / "snapshots"),
        parser=MarkdownBlockParser(),
        safety_scanner=DocumentSafetyScanner(),
        service=IngestionService(factory, vault_id_hash=vault_hash),
        vault_hash=vault_hash,
    )
    run_id = IngestionRunId.generate()
    original_apply = runner.apply_pending
    attempts = 0

    async def fail_once(selected_run_id: IngestionRunId) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic interruption")
        return await original_apply(selected_run_id)

    monkeypatch.setattr(runner, "apply_pending", fail_once)
    try:
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            await run_ingestion_workflow(runner, checkpoint_path, run_id=run_id)

        report = await run_ingestion_workflow(runner, checkpoint_path, run_id=run_id)

        assert report.status is IngestionRunStatus.COMPLETED
        assert report.total == report.succeeded == 1
        async with engine.connect() as connection:
            item_count = await connection.scalar(
                select(func.count(IngestionItemTable.id)).where(IngestionItemTable.run_id == run_id)
            )
        assert item_count == 1
        with sqlite3.connect(checkpoint_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            dump = "\n".join(connection.iterdump())
        assert {"checkpoints", "writes"} <= tables
        assert private_body not in dump
        assert str(vault) not in dump
    finally:
        await engine.dispose()
