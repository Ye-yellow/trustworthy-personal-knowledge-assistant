"""Create the L2 ingestion ledger and source-location schema.

Revision ID: 20260728_0002
Revises: 20260728_0001
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260728_0002"
down_revision: str | None = "20260728_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_DDL = (
    """CREATE TABLE ingestion_runs (
        id VARCHAR(33) NOT NULL,
        vault_id_hash VARCHAR(64) NOT NULL,
        scan_scope_hash VARCHAR(64) NOT NULL,
        manifest_hash VARCHAR(64) NOT NULL,
        status VARCHAR(14) DEFAULT 'PLANNING' NOT NULL,
        total_items INTEGER DEFAULT 0 NOT NULL,
        succeeded_items INTEGER DEFAULT 0 NOT NULL,
        skipped_items INTEGER DEFAULT 0 NOT NULL,
        quarantined_items INTEGER DEFAULT 0 NOT NULL,
        failed_items INTEGER DEFAULT 0 NOT NULL,
        error_category VARCHAR(100),
        started_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        completed_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_ingestion_runs PRIMARY KEY (id),
        CONSTRAINT ck_ingestion_runs_ingestion_run_id_prefix
            CHECK (id LIKE 'ingrun_%' AND length(id) = 33),
        CONSTRAINT ck_ingestion_runs_ingestion_run_vault_hash
            CHECK (length(vault_id_hash) = 64 AND vault_id_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_ingestion_runs_ingestion_run_scope_hash
            CHECK (length(scan_scope_hash) = 64 AND scan_scope_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_ingestion_runs_ingestion_run_manifest_hash
            CHECK (length(manifest_hash) = 64 AND manifest_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_ingestion_runs_ingestion_run_counts_nonnegative CHECK (
            total_items >= 0 AND succeeded_items >= 0 AND skipped_items >= 0
            AND quarantined_items >= 0 AND failed_items >= 0
        ),
        CONSTRAINT ck_ingestion_runs_ingestion_run_counts_bounded CHECK (
            succeeded_items + skipped_items + quarantined_items + failed_items <= total_items
        ),
        CONSTRAINT ck_ingestion_runs_ingestion_run_completed_after_start
            CHECK (completed_at IS NULL OR completed_at >= started_at),
        CONSTRAINT ck_ingestion_runs_ingestion_run_revision_positive CHECK (revision >= 1),
        CONSTRAINT ck_ingestion_runs_ingestion_run_status CHECK (status IN (
            'PLANNING', 'APPLYING', 'COMPLETED', 'PARTIAL_FAILED', 'FAILED', 'ABANDONED'
        ))
    )""",
    """CREATE TABLE source_locations (
        source_id VARCHAR(33) NOT NULL,
        vault_id_hash VARCHAR(64) NOT NULL,
        relative_path VARCHAR(2048) NOT NULL,
        path_key VARCHAR(64) NOT NULL,
        file_key VARCHAR(64),
        last_seen_run_id VARCHAR(33),
        observed_size INTEGER NOT NULL,
        observed_mtime_ns INTEGER NOT NULL,
        deleted_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_source_locations PRIMARY KEY (source_id),
        CONSTRAINT ck_source_locations_source_location_vault_hash
            CHECK (length(vault_id_hash) = 64 AND vault_id_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_source_locations_source_location_path_not_empty
            CHECK (length(relative_path) > 0),
        CONSTRAINT ck_source_locations_source_location_path_key
            CHECK (length(path_key) = 64 AND path_key NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_source_locations_source_location_file_key CHECK (
            file_key IS NULL OR (length(file_key) = 64 AND file_key NOT GLOB '*[^0-9a-f]*')
        ),
        CONSTRAINT ck_source_locations_source_location_observation_nonnegative
            CHECK (observed_size >= 0 AND observed_mtime_ns >= 0),
        CONSTRAINT ck_source_locations_source_location_revision_positive CHECK (revision >= 1),
        CONSTRAINT fk_source_locations_source_id_sources FOREIGN KEY(source_id)
            REFERENCES sources (id) ON DELETE RESTRICT,
        CONSTRAINT fk_source_locations_last_seen_run_id_ingestion_runs FOREIGN KEY(last_seen_run_id)
            REFERENCES ingestion_runs (id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE ingestion_items (
        id VARCHAR(34) NOT NULL,
        run_id VARCHAR(33) NOT NULL,
        source_id VARCHAR(33),
        action VARCHAR(9) NOT NULL,
        relative_path VARCHAR(2048) NOT NULL,
        path_key VARCHAR(64) NOT NULL,
        file_key VARCHAR(64),
        content_hash VARCHAR(64),
        base_version_id VARCHAR(33),
        result_version_id VARCHAR(33),
        status VARCHAR(11) DEFAULT 'PENDING' NOT NULL,
        operation_id VARCHAR(255) NOT NULL,
        attempt INTEGER DEFAULT 1 NOT NULL,
        error_category VARCHAR(100),
        safety_signals_json JSON DEFAULT '{}' NOT NULL,
        completed_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_ingestion_items PRIMARY KEY (id),
        CONSTRAINT ck_ingestion_items_ingestion_item_id_prefix
            CHECK (id LIKE 'ingitem_%' AND length(id) = 34),
        CONSTRAINT ck_ingestion_items_ingestion_item_path_not_empty
            CHECK (length(relative_path) > 0),
        CONSTRAINT ck_ingestion_items_ingestion_item_path_key
            CHECK (length(path_key) = 64 AND path_key NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_ingestion_items_ingestion_item_file_key CHECK (
            file_key IS NULL OR (length(file_key) = 64 AND file_key NOT GLOB '*[^0-9a-f]*')
        ),
        CONSTRAINT ck_ingestion_items_ingestion_item_content_hash CHECK (
            content_hash IS NULL OR (
                length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'
            )
        ),
        CONSTRAINT ck_ingestion_items_ingestion_item_operation_not_empty
            CHECK (length(operation_id) > 0),
        CONSTRAINT ck_ingestion_items_ingestion_item_attempt_positive CHECK (attempt >= 1),
        CONSTRAINT ck_ingestion_items_ingestion_item_signals_json_valid
            CHECK (json_valid(safety_signals_json)),
        CONSTRAINT ck_ingestion_items_ingestion_item_revision_positive CHECK (revision >= 1),
        CONSTRAINT uq_ingestion_items_run_path_action UNIQUE (run_id, path_key, action),
        CONSTRAINT uq_ingestion_items_operation UNIQUE (operation_id),
        CONSTRAINT fk_ingestion_items_run_id_ingestion_runs FOREIGN KEY(run_id)
            REFERENCES ingestion_runs (id) ON DELETE RESTRICT,
        CONSTRAINT fk_ingestion_items_source_id_sources FOREIGN KEY(source_id)
            REFERENCES sources (id) ON DELETE RESTRICT,
        CONSTRAINT fk_ingestion_items_base_version_id_source_versions FOREIGN KEY(base_version_id)
            REFERENCES source_versions (id) ON DELETE RESTRICT,
        CONSTRAINT fk_ingestion_items_result_version_id_source_versions
            FOREIGN KEY(result_version_id)
            REFERENCES source_versions (id) ON DELETE RESTRICT,
        CONSTRAINT ck_ingestion_items_ingestion_action CHECK (action IN (
            'CREATED', 'UPDATED', 'MOVED', 'DELETED', 'UNCHANGED'
        )),
        CONSTRAINT ck_ingestion_items_ingestion_item_status CHECK (status IN (
            'PENDING', 'APPLYING', 'SUCCEEDED', 'SKIPPED', 'QUARANTINED', 'FAILED'
        ))
    )""",
)

_INDEX_DDL = (
    """CREATE UNIQUE INDEX uq_ingestion_runs_one_active
        ON ingestion_runs (vault_id_hash) WHERE status IN ('PLANNING', 'APPLYING')""",
    "CREATE INDEX ix_source_locations_vault_id_hash ON source_locations (vault_id_hash)",
    "CREATE INDEX ix_source_locations_file_key ON source_locations (file_key)",
    """CREATE UNIQUE INDEX uq_source_locations_live_path
        ON source_locations (vault_id_hash, path_key) WHERE deleted_at IS NULL""",
    "CREATE INDEX ix_ingestion_items_run_id ON ingestion_items (run_id)",
    "CREATE INDEX ix_ingestion_items_run_status ON ingestion_items (run_id, status)",
)


def upgrade() -> None:
    for statement in (*_TABLE_DDL, *_INDEX_DDL):
        op.execute(statement)


def downgrade() -> None:
    for table_name in ("ingestion_items", "source_locations", "ingestion_runs"):
        op.execute(f'DROP TABLE IF EXISTS "{table_name}"')
