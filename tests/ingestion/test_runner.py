from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import IngestionAction, IngestionRunStatus
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
)
from trustworthy_kb.ingestion.service import IngestionService
from trustworthy_kb.persistence import (
    Base,
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)
from trustworthy_kb.persistence.ingestion_tables import IngestionItemTable


class FilesystemInventory:
    def __init__(self, vault: Path) -> None:
        self._vault = vault

    async def inventory(self) -> VaultInventoryResult:
        observations = []
        for path in self._vault.rglob("*.md"):
            relative_path = path.relative_to(self._vault).as_posix()
            stat = path.stat()
            observations.append(
                VaultFileObservation(
                    relative_path=relative_path,
                    path_key=path_key(relative_path),
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    file_key=file_key(stat.st_dev, stat.st_ino),
                )
            )
        return VaultInventoryResult(
            complete=True,
            files=tuple(sorted(observations, key=lambda item: item.path_key)),
        )


@pytest.mark.asyncio
async def test_manual_runner_reconciles_full_scan_change_sequence(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("# Synthetic\n\nVersion one", encoding="utf-8")
    database_path = tmp_path / "runner.db"
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    vault_hash = "a" * 64
    runner = ManualIngestionRunner(
        inventory=FilesystemInventory(vault),
        unit_of_work_factory=factory,
        reader=StableMarkdownReader(vault, max_bytes=1024 * 1024, interval_ms=0),
        snapshots=ContentAddressedSnapshotStore(tmp_path / "snapshots"),
        parser=MarkdownBlockParser(),
        safety_scanner=DocumentSafetyScanner(),
        service=IngestionService(factory, vault_id_hash=vault_hash),
        vault_hash=vault_hash,
    )
    try:
        reports = [await runner.run()]
        reports.append(await runner.run())
        note.write_text("# Synthetic\n\nVersion two", encoding="utf-8")
        reports.append(await runner.run())
        moved = vault / "moved.md"
        note.rename(moved)
        reports.append(await runner.run())
        moved.unlink()
        reports.append(await runner.run())

        assert all(report.status is IngestionRunStatus.COMPLETED for report in reports)
        async with engine.connect() as connection:
            actions = []
            for report in reports:
                rows = await connection.execute(
                    select(IngestionItemTable.action).where(
                        IngestionItemTable.run_id == report.run_id
                    )
                )
                actions.append(tuple(rows.scalars()))
        assert actions == [
            (IngestionAction.CREATED,),
            (IngestionAction.UNCHANGED,),
            (IngestionAction.UPDATED,),
            (IngestionAction.MOVED,),
            (IngestionAction.DELETED,),
        ]
        snapshots = list((tmp_path / "snapshots" / "sha256").glob("*/*.md"))
        assert len(snapshots) == 2
        with sqlite3.connect(database_path) as connection:
            dump = "\n".join(connection.iterdump())
        assert "Version one" not in dump
        assert "Version two" not in dump
    finally:
        await engine.dispose()
