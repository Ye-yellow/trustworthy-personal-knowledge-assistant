from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.exc import DatabaseError

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import OperationLogId, SourceId
from trustworthy_kb.persistence import Base, create_database_engine
from trustworthy_kb.persistence.errors import DatabaseSchemaMismatchError
from trustworthy_kb.persistence.migrations import assert_schema_current

PROJECT_ROOT = Path(__file__).parents[2]
EXPECTED_CONTROL_TABLES = {
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


def migration_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    return config


def test_upgrade_downgrade_and_reupgrade_create_complete_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = migration_config(database_path)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        assert set(inspect(engine).get_table_names()) == EXPECTED_CONTROL_TABLES | {
            "alembic_version"
        }
        with engine.connect() as connection:
            triggers = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                )
            }
        assert triggers == {"trg_operation_logs_no_delete", "trg_operation_logs_no_update"}

        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}

        command.upgrade(config, "head")
        assert set(inspect(engine).get_table_names()) == EXPECTED_CONTROL_TABLES | {
            "alembic_version"
        }
    finally:
        engine.dispose()


def test_online_migration_creates_missing_database_parent(tmp_path: Path) -> None:
    database_path = tmp_path / "new" / "nested" / "migration.db"

    command.upgrade(migration_config(database_path), "head")

    assert database_path.is_file()


def test_operation_log_migration_triggers_reject_update_and_delete(tmp_path: Path) -> None:
    database_path = tmp_path / "immutable.db"
    command.upgrade(migration_config(database_path), "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    source_id = str(SourceId.generate())
    log_id = str(OperationLogId.generate())
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO sources "
                    "(id, source_type, canonical_uri, owner, trust_tier, sensitivity) "
                    "VALUES (:id, 'user_input', 'user://migration-test', 'test', 'T0', 'private')"
                ),
                {"id": source_id},
            )
            connection.execute(
                text(
                    "INSERT INTO operation_logs "
                    "(id, operation_id, step_number, actor_type, action, target_type, "
                    "target_id, before_json, after_json, entry_hash) "
                    "VALUES (:id, 'op-test', 0, 'SYSTEM', 'CREATE', 'source', :target_id, "
                    "'{}', '{}', :entry_hash)"
                ),
                {"id": log_id, "target_id": source_id, "entry_hash": "a" * 64},
            )

        with pytest.raises(DatabaseError, match="operation_logs are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE operation_logs SET action = 'ALTER' WHERE id = :id"),
                    {"id": log_id},
                )
        with pytest.raises(DatabaseError, match="operation_logs are append-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM operation_logs WHERE id = :id"),
                    {"id": log_id},
                )
    finally:
        engine.dispose()


def test_migrated_schema_matches_orm_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "metadata-match.db"
    command.upgrade(migration_config(database_path), "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        schema = inspect(engine)
        for table_name, table in Base.metadata.tables.items():
            assert {column["name"] for column in schema.get_columns(table_name)} == set(
                table.columns.keys()
            )
            assert {index["name"] for index in schema.get_indexes(table_name)} == {
                index.name for index in table.indexes
            }
            expected_unique_columns = {
                tuple(column.name for column in constraint.columns)
                for constraint in table.constraints
                if isinstance(constraint, UniqueConstraint)
            }
            expected_unique_columns.update(
                tuple(column.name for column in index.columns)
                for index in table.indexes
                if index.unique
            )
            with engine.connect() as connection:
                indexes = connection.exec_driver_sql(
                    f'PRAGMA index_list("{table_name}")'
                ).mappings()
                migrated_unique_columns = {
                    tuple(
                        str(column["name"])
                        for column in connection.exec_driver_sql(
                            f'PRAGMA index_info("{index["name"]}")'
                        ).mappings()
                    )
                    for index in indexes
                    if index["unique"] and index["origin"] != "pk"
                }
            assert migrated_unique_columns == expected_unique_columns
            assert {
                constraint["name"] for constraint in schema.get_check_constraints(table_name)
            } == {
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            }

            with engine.connect() as connection:
                pragma_foreign_keys = connection.exec_driver_sql(
                    f'PRAGMA foreign_key_list("{table_name}")'
                ).mappings()
                migrated_foreign_keys = {
                    (
                        (str(foreign_key["from"]),),
                        str(foreign_key["table"]),
                        (str(foreign_key["to"]),),
                        str(foreign_key["on_delete"]),
                    )
                    for foreign_key in pragma_foreign_keys
                }
            metadata_foreign_keys = {
                (
                    tuple(column.name for column in foreign_key.columns),
                    next(iter(foreign_key.elements)).column.table.name,
                    tuple(element.column.name for element in foreign_key.elements),
                    foreign_key.ondelete,
                )
                for foreign_key in table.foreign_key_constraints
            }
            assert migrated_foreign_keys == metadata_foreign_keys
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_schema_head_check_reports_safe_mismatch_and_accepts_head(tmp_path: Path) -> None:
    database_path = tmp_path / "head-check.db"
    config = migration_config(database_path)
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    )
    try:
        with pytest.raises(DatabaseSchemaMismatchError) as captured:
            await assert_schema_current(engine, config)
        assert str(database_path) not in str(captured.value)
        assert "uv run alembic upgrade head" in str(captured.value)

        await asyncio.to_thread(command.upgrade, config, "head")
        await assert_schema_current(engine, config)
    finally:
        await engine.dispose()


def test_initial_migration_supports_offline_sql(tmp_path: Path) -> None:
    config = migration_config(tmp_path / "offline.db")
    output = StringIO()
    config.output_buffer = output

    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE sources" in sql
    assert "CREATE TABLE operation_logs" in sql
    assert "CREATE TABLE ingestion_runs" in sql
    assert "CREATE TABLE source_locations" in sql
    assert "CREATE TABLE governance_runs" in sql
    assert "CREATE TABLE review_requests" in sql
    assert "trg_operation_logs_no_update" in sql
