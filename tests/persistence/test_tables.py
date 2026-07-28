from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import Sensitivity, SourceId, SourceType, TrustTier
from trustworthy_kb.persistence import Base, create_database_engine, create_session_factory
from trustworthy_kb.persistence.source_tables import SourceTable

EXPECTED_TABLES = {
    "claim_origins",
    "claims",
    "content_blocks",
    "curated_versions",
    "evidence",
    "evidence_families",
    "governance_items",
    "governance_runs",
    "idempotency_records",
    "ingestion_items",
    "ingestion_runs",
    "index_generations",
    "index_jobs",
    "knowledge_changes",
    "knowledge_notes",
    "lineage_edges",
    "model_runs",
    "operation_logs",
    "quality_check_evidence",
    "quality_checks",
    "review_requests",
    "source_versions",
    "sources",
    "source_locations",
}


def test_metadata_declares_complete_control_plane_and_restrictive_foreign_keys() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES

    for table in Base.metadata.tables.values():
        for foreign_key in table.foreign_key_constraints:
            assert foreign_key.ondelete == "RESTRICT"


@pytest.mark.asyncio
async def test_metadata_creates_all_tables_indexes_and_checks(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'schema.db').as_posix()}")
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

            def inspect_schema(sync_connection: object) -> tuple[set[str], set[str]]:
                schema = inspect(sync_connection)
                table_names = set(schema.get_table_names())
                index_names = {
                    index["name"]
                    for table_name in table_names
                    for index in schema.get_indexes(table_name)
                    if index["name"] is not None
                }
                return table_names, index_names

            table_names, index_names = await connection.run_sync(inspect_schema)

        assert table_names == EXPECTED_TABLES
        assert "uq_index_generations_one_active" in index_names
        assert "uq_knowledge_notes_live_path" in index_names
        assert "uq_ingestion_runs_one_active" in index_names
        assert "uq_source_locations_live_path" in index_names
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_table_round_trips_typed_id(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'roundtrip.db').as_posix()}")
    )
    session_factory = create_session_factory(engine)
    source_id = SourceId.generate()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add(
                SourceTable(
                    id=source_id,
                    source_type=SourceType.OBSIDIAN_MARKDOWN,
                    canonical_uri="obsidian://vault/example",
                    owner="local-user",
                    trust_tier=TrustTier.T0,
                    sensitivity=Sensitivity.PRIVATE,
                )
            )
            await session.commit()
        async with session_factory() as session:
            restored = await session.scalar(select(SourceTable.id))

        assert restored == source_id
        assert isinstance(restored, SourceId)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_rejects_invalid_id_prefix_and_enum(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'checks.db').as_posix()}")
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        statement = text(
            "INSERT INTO sources "
            "(id, source_type, canonical_uri, owner, trust_tier, sensitivity) "
            "VALUES (:id, :source_type, :uri, :owner, :trust_tier, :sensitivity)"
        )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    statement,
                    {
                        "id": "claim_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                        "source_type": SourceType.WEB_PAGE.value,
                        "uri": "https://example.invalid/invalid-id",
                        "owner": "local-user",
                        "trust_tier": TrustTier.T3.value,
                        "sensitivity": Sensitivity.PRIVATE.value,
                    },
                )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    statement,
                    {
                        "id": str(SourceId.generate()),
                        "source_type": "unsupported",
                        "uri": "https://example.invalid/invalid-enum",
                        "owner": "local-user",
                        "trust_tier": TrustTier.T3.value,
                        "sensitivity": Sensitivity.PRIVATE.value,
                    },
                )
    finally:
        await engine.dispose()
