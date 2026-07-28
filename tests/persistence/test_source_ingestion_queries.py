from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    ContentBlockId,
    ContentBlockRecord,
    IngestionRunId,
    IngestionRunRecord,
    IngestionRunStatus,
    Sensitivity,
    SourceId,
    SourceLocationRecord,
    SourceRecord,
    SourceType,
    SourceVersionId,
    SourceVersionRecord,
    SourceVersionStatus,
    TrustTier,
)
from trustworthy_kb.persistence import Base, create_database_engine, create_session_factory
from trustworthy_kb.persistence.ingestion_repository import IngestionRepository
from trustworthy_kb.persistence.source_repository import SourceRepository


def now() -> datetime:
    return datetime.now(UTC)


@pytest.mark.asyncio
async def test_source_repository_exposes_ingestion_queries_and_move(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'source-query.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    sources = SourceRepository(session)
    ingestion = IngestionRepository(session)
    timestamp = now()
    source = SourceRecord(
        id=SourceId.generate(),
        source_type=SourceType.OBSIDIAN_MARKDOWN,
        canonical_uri="obsidian://vault/synthetic/note.md",
        owner="synthetic-owner",
        trust_tier=TrustTier.T0,
        sensitivity=Sensitivity.PRIVATE,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )
    run = IngestionRunRecord(
        id=IngestionRunId.generate(),
        vault_id_hash="a" * 64,
        scan_scope_hash="b" * 64,
        manifest_hash="c" * 64,
        status=IngestionRunStatus.PLANNING,
        revision=1,
        started_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )
    version = SourceVersionRecord(
        id=SourceVersionId.generate(),
        source_id=source.id,
        version_number=1,
        content_hash="d" * 64,
        byte_size=12,
        media_type="text/markdown",
        captured_at=timestamp,
        original_path="Synthetic/note.md",
        status=SourceVersionStatus.CAPTURED,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )
    block = ContentBlockRecord(
        id=ContentBlockId.generate(),
        source_version_id=version.id,
        ordinal=0,
        block_type="paragraph",
        anchor="paragraph:1",
        text_hash="e" * 64,
        character_count=9,
        created_at=timestamp,
    )
    try:
        await sources.add_source(source)
        await ingestion.begin_run(run)
        await ingestion.record_source_location(
            SourceLocationRecord(
                source_id=source.id,
                vault_id_hash=run.vault_id_hash,
                relative_path="Synthetic/note.md",
                path_key="f" * 64,
                last_seen_run_id=run.id,
                observed_size=12,
                observed_mtime_ns=100,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        assert await sources.get_current_source_version(source.id) is None
        assert await sources.get_latest_source_version(source.id) is None
        assert await sources.find_source_version_by_hash(source.id, version.content_hash) is None
        await sources.append_source_version(version)
        await sources.add_content_blocks([block])
        assert await sources.get_source_version(version.id) == version
        assert await sources.get_latest_source_version(source.id) == version
        assert await sources.find_source_version_by_hash(source.id, version.content_hash) == version
        assert await sources.list_content_blocks(version.id) == (block,)
        parsed = await sources.transition_source_version(
            version.id, SourceVersionStatus.PARSED, expected_revision=1
        )
        ready = await sources.transition_source_version(
            version.id, SourceVersionStatus.READY, expected_revision=parsed.revision
        )
        activated = await sources.activate_source_version(
            source.id, ready.id, expected_revision=source.revision
        )
        current = await sources.get_current_source_version(source.id)
        assert current is not None
        assert current.id == ready.id
        assert await sources.find_source_by_location(run.vault_id_hash, "f" * 64) == activated
        assert await sources.list_live_sources_for_vault(run.vault_id_hash) == (activated,)
        moved = await sources.move_source(
            source.id,
            "obsidian://vault/synthetic/moved.md",
            expected_revision=activated.revision,
        )
        assert moved.canonical_uri.endswith("moved.md")
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
