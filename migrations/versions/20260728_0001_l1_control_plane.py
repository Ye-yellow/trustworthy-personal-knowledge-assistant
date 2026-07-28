"""Create the L1 SQLite control-plane schema.

Revision ID: 20260728_0001
Revises:
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260728_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENTITY_PREFIXES = (
    ("source", "source_"),
    ("source_version", "srcver_"),
    ("content_block", "block_"),
    ("claim", "claim_"),
    ("evidence", "evidence_"),
    ("quality_check", "qcheck_"),
    ("knowledge_change", "change_"),
    ("knowledge_note", "note_"),
    ("curated_version", "curated_"),
    ("index_generation", "idxgen_"),
    ("index_job", "idxjob_"),
    ("model_run", "modelrun_"),
)
_ENTITY_VALUES = ", ".join(f"'{entity_type}'" for entity_type, _ in _ENTITY_PREFIXES)


def _entity_id_check(type_column: str, id_column: str) -> str:
    return " OR ".join(
        f"({type_column} = '{entity_type}' AND {id_column} LIKE '{prefix}%')"
        for entity_type, prefix in _ENTITY_PREFIXES
    )


_TABLE_DDL = (
    """CREATE TABLE claim_origins (
        claim_id VARCHAR(32) NOT NULL,
        content_block_id VARCHAR(32) NOT NULL,
        model_run_id VARCHAR(35),
        origin_span_json JSON NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_claim_origins PRIMARY KEY (claim_id, content_block_id),
        CONSTRAINT ck_claim_origins_claim_origin_span_json_valid
            CHECK (json_valid(origin_span_json)),
        CONSTRAINT fk_claim_origins_claim_id_claims FOREIGN KEY(claim_id)
            REFERENCES claims (id) ON DELETE RESTRICT,
        CONSTRAINT fk_claim_origins_content_block_id_content_blocks FOREIGN KEY(content_block_id)
            REFERENCES content_blocks (id) ON DELETE RESTRICT,
        CONSTRAINT fk_claim_origins_model_run_id_model_runs FOREIGN KEY(model_run_id)
            REFERENCES model_runs (id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE claims (
        id VARCHAR(32) NOT NULL,
        claim_type VARCHAR(15) NOT NULL,
        subject VARCHAR(1024) NOT NULL,
        predicate VARCHAR(512) NOT NULL,
        object_json JSON NOT NULL,
        scope_json JSON NOT NULL,
        valid_from VARCHAR(27),
        valid_to VARCHAR(27),
        freshness_at VARCHAR(27),
        sensitivity VARCHAR(10) NOT NULL,
        status VARCHAR(13) DEFAULT 'PROPOSED' NOT NULL,
        current_quality_check_id VARCHAR(33),
        superseded_by_id VARCHAR(32),
        deleted_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_claims PRIMARY KEY (id),
        CONSTRAINT ck_claims_claim_id_prefix
            CHECK (id LIKE 'claim_%' AND length(id) = 32),
        CONSTRAINT ck_claims_claim_subject_not_empty CHECK (length(subject) > 0),
        CONSTRAINT ck_claims_claim_predicate_not_empty CHECK (length(predicate) > 0),
        CONSTRAINT ck_claims_claim_object_json_valid CHECK (json_valid(object_json)),
        CONSTRAINT ck_claims_claim_scope_json_valid CHECK (json_valid(scope_json)),
        CONSTRAINT ck_claims_claim_valid_range
            CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CONSTRAINT ck_claims_claim_not_self_superseded
            CHECK (superseded_by_id IS NULL OR superseded_by_id <> id),
        CONSTRAINT ck_claims_claim_revision_positive CHECK (revision >= 1),
        CONSTRAINT ck_claims_claim_type CHECK (claim_type IN (
            'FACT', 'DEFINITION', 'PROCEDURE', 'USER_EXPERIENCE', 'PREFERENCE',
            'DECISION', 'PREDICTION', 'CODE_BEHAVIOR'
        )),
        CONSTRAINT ck_claims_claim_sensitivity
            CHECK (sensitivity IN ('private', 'restricted', 'public')),
        CONSTRAINT ck_claims_claim_status CHECK (status IN (
            'PROPOSED', 'VERIFIED', 'USER_ASSERTED', 'OPINION', 'INSUFFICIENT',
            'CONTESTED', 'OUTDATED', 'REJECTED', 'SUPERSEDED'
        )),
        CONSTRAINT fk_claims_current_quality_check_id_quality_checks
            FOREIGN KEY(current_quality_check_id) REFERENCES quality_checks (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_claims_superseded_by_id_claims FOREIGN KEY(superseded_by_id)
            REFERENCES claims (id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE content_blocks (
        id VARCHAR(32) NOT NULL,
        source_version_id VARCHAR(33) NOT NULL,
        ordinal INTEGER NOT NULL,
        block_type VARCHAR(100) NOT NULL,
        anchor VARCHAR(1024) NOT NULL,
        text_hash VARCHAR(64) NOT NULL,
        character_count INTEGER NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_content_blocks PRIMARY KEY (id),
        CONSTRAINT ck_content_blocks_content_block_id_prefix
            CHECK (id LIKE 'block_%' AND length(id) = 32),
        CONSTRAINT ck_content_blocks_content_block_ordinal_nonnegative CHECK (ordinal >= 0),
        CONSTRAINT ck_content_blocks_content_block_type_not_empty CHECK (length(block_type) > 0),
        CONSTRAINT ck_content_blocks_content_block_anchor_not_empty CHECK (length(anchor) > 0),
        CONSTRAINT ck_content_blocks_content_block_text_hash
            CHECK (length(text_hash) = 64 AND text_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_content_blocks_content_block_character_count_nonnegative
            CHECK (character_count >= 0),
        CONSTRAINT uq_content_blocks_ordinal UNIQUE (source_version_id, ordinal),
        CONSTRAINT uq_content_blocks_anchor UNIQUE (source_version_id, anchor),
        CONSTRAINT fk_content_blocks_source_version_id_source_versions
            FOREIGN KEY(source_version_id) REFERENCES source_versions (id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE curated_versions (
        id VARCHAR(34) NOT NULL,
        note_id VARCHAR(31) NOT NULL,
        version_number INTEGER NOT NULL,
        based_on_change_id VARCHAR(33) NOT NULL,
        content_hash VARCHAR(64) NOT NULL,
        vault_path VARCHAR(2048) NOT NULL,
        status VARCHAR(20) DEFAULT 'DRAFT' NOT NULL,
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_curated_versions PRIMARY KEY (id),
        CONSTRAINT ck_curated_versions_curated_version_id_prefix
            CHECK (id LIKE 'curated_%' AND length(id) = 34),
        CONSTRAINT ck_curated_versions_curated_version_number_positive
            CHECK (version_number >= 1),
        CONSTRAINT ck_curated_versions_curated_version_content_hash
            CHECK (length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_curated_versions_curated_version_path_not_empty
            CHECK (length(vault_path) > 0),
        CONSTRAINT ck_curated_versions_curated_version_revision_positive CHECK (revision >= 1),
        CONSTRAINT uq_curated_versions_number UNIQUE (note_id, version_number),
        CONSTRAINT uq_curated_versions_content UNIQUE (note_id, content_hash),
        CONSTRAINT fk_curated_versions_note_id_knowledge_notes FOREIGN KEY(note_id)
            REFERENCES knowledge_notes (id) ON DELETE RESTRICT,
        CONSTRAINT fk_curated_versions_based_on_change_id_knowledge_changes
            FOREIGN KEY(based_on_change_id) REFERENCES knowledge_changes (id) ON DELETE RESTRICT,
        CONSTRAINT ck_curated_versions_curated_version_status CHECK (status IN (
            'DRAFT', 'VALIDATING', 'STAGING', 'ACTIVE', 'STALE_PENDING_REVIEW',
            'SUPERSEDED', 'QUARANTINED', 'FAILED'
        ))
    )""",
    """CREATE TABLE evidence (
        id VARCHAR(35) NOT NULL,
        claim_id VARCHAR(32) NOT NULL,
        source_version_id VARCHAR(33) NOT NULL,
        evidence_family_id VARCHAR(32) NOT NULL,
        anchor VARCHAR(1024) NOT NULL,
        stance VARCHAR(11) NOT NULL,
        excerpt_hash VARCHAR(64) NOT NULL,
        relevance_score FLOAT NOT NULL,
        independence_score FLOAT NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_evidence PRIMARY KEY (id),
        CONSTRAINT ck_evidence_evidence_id_prefix
            CHECK (id LIKE 'evidence_%' AND length(id) = 35),
        CONSTRAINT ck_evidence_evidence_anchor_not_empty CHECK (length(anchor) > 0),
        CONSTRAINT ck_evidence_evidence_excerpt_hash
            CHECK (length(excerpt_hash) = 64 AND excerpt_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_evidence_evidence_relevance_range
            CHECK (relevance_score >= 0 AND relevance_score <= 1),
        CONSTRAINT ck_evidence_evidence_independence_range
            CHECK (independence_score >= 0 AND independence_score <= 1),
        CONSTRAINT uq_evidence_claim_location_stance
            UNIQUE (claim_id, source_version_id, anchor, stance),
        CONSTRAINT fk_evidence_claim_id_claims FOREIGN KEY(claim_id)
            REFERENCES claims (id) ON DELETE RESTRICT,
        CONSTRAINT fk_evidence_source_version_id_source_versions FOREIGN KEY(source_version_id)
            REFERENCES source_versions (id) ON DELETE RESTRICT,
        CONSTRAINT fk_evidence_evidence_family_id_evidence_families
            FOREIGN KEY(evidence_family_id) REFERENCES evidence_families (id) ON DELETE RESTRICT,
        CONSTRAINT ck_evidence_evidence_stance
            CHECK (stance IN ('SUPPORTS', 'CONTRADICTS', 'NEUTRAL'))
    )""",
    """CREATE TABLE evidence_families (
        id VARCHAR(32) NOT NULL,
        canonical_origin VARCHAR(2048) NOT NULL,
        origin_fingerprint VARCHAR(64) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_evidence_families PRIMARY KEY (id),
        CONSTRAINT ck_evidence_families_evidence_family_id_prefix
            CHECK (id LIKE 'evfam_%' AND length(id) = 32),
        CONSTRAINT ck_evidence_families_evidence_family_origin_not_empty
            CHECK (length(canonical_origin) > 0),
        CONSTRAINT ck_evidence_families_evidence_family_fingerprint CHECK (
            length(origin_fingerprint) = 64
            AND origin_fingerprint NOT GLOB '*[^0-9a-f]*'
        ),
        CONSTRAINT uq_evidence_families_origin_fingerprint UNIQUE (origin_fingerprint)
    )""",
    f"""CREATE TABLE idempotency_records (
        id VARCHAR(31) NOT NULL,
        scope VARCHAR(255) NOT NULL,
        idempotency_key VARCHAR(512) NOT NULL,
        request_hash VARCHAR(64) NOT NULL,
        status VARCHAR(11) DEFAULT 'IN_PROGRESS' NOT NULL,
        result_type VARCHAR(16),
        result_id VARCHAR(64),
        lease_owner VARCHAR(255),
        lease_expires_at VARCHAR(27),
        attempt INTEGER DEFAULT '0' NOT NULL,
        error_category VARCHAR(100),
        expires_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_idempotency_records PRIMARY KEY (id),
        CONSTRAINT ck_idempotency_records_idempotency_record_id_prefix
            CHECK (id LIKE 'idem_%' AND length(id) = 31),
        CONSTRAINT ck_idempotency_records_idempotency_scope_not_empty CHECK (length(scope) > 0),
        CONSTRAINT ck_idempotency_records_idempotency_key_not_empty
            CHECK (length(idempotency_key) > 0),
        CONSTRAINT ck_idempotency_records_idempotency_request_hash
            CHECK (length(request_hash) = 64 AND request_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_idempotency_records_idempotency_result_pair CHECK (
            (result_type IS NULL AND result_id IS NULL)
            OR (result_type IS NOT NULL AND result_id IS NOT NULL)
        ),
        CONSTRAINT ck_idempotency_records_idempotency_result_id_type
            CHECK (result_id IS NULL OR ({_entity_id_check("result_type", "result_id")})),
        CONSTRAINT ck_idempotency_records_idempotency_attempt_nonnegative CHECK (attempt >= 0),
        CONSTRAINT ck_idempotency_records_idempotency_revision_positive CHECK (revision >= 1),
        CONSTRAINT uq_idempotency_records_key UNIQUE (scope, idempotency_key),
        CONSTRAINT ck_idempotency_records_idempotency_status
            CHECK (status IN ('IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'UNKNOWN')),
        CONSTRAINT ck_idempotency_records_idempotency_result_type
            CHECK (result_type IN ({_ENTITY_VALUES}))
    )""",
    """CREATE TABLE index_generations (
        id VARCHAR(33) NOT NULL,
        generation_number INTEGER NOT NULL,
        embedding_model VARCHAR(255) NOT NULL,
        chunker_version VARCHAR(100) NOT NULL,
        status VARCHAR(10) DEFAULT 'STAGING' NOT NULL,
        activated_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_index_generations PRIMARY KEY (id),
        CONSTRAINT ck_index_generations_index_generation_id_prefix
            CHECK (id LIKE 'idxgen_%' AND length(id) = 33),
        CONSTRAINT ck_index_generations_index_generation_number_positive
            CHECK (generation_number >= 1),
        CONSTRAINT ck_index_generations_index_generation_embedding_model_not_empty
            CHECK (length(embedding_model) > 0),
        CONSTRAINT ck_index_generations_index_generation_chunker_version_not_empty
            CHECK (length(chunker_version) > 0),
        CONSTRAINT ck_index_generations_index_generation_revision_positive CHECK (revision >= 1),
        CONSTRAINT uq_index_generations_generation_number UNIQUE (generation_number),
        CONSTRAINT ck_index_generations_index_generation_status
            CHECK (status IN ('STAGING', 'ACTIVE', 'SUPERSEDED', 'FAILED'))
    )""",
    f"""CREATE TABLE index_jobs (
        id VARCHAR(33) NOT NULL,
        object_type VARCHAR(16) NOT NULL,
        object_id VARCHAR(64) NOT NULL,
        generation_id VARCHAR(33) NOT NULL,
        status VARCHAR(14) DEFAULT 'PENDING' NOT NULL,
        attempt INTEGER DEFAULT '0' NOT NULL,
        error_category VARCHAR(100),
        lease_owner VARCHAR(255),
        lease_expires_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_index_jobs PRIMARY KEY (id),
        CONSTRAINT ck_index_jobs_index_job_id_prefix
            CHECK (id LIKE 'idxjob_%' AND length(id) = 33),
        CONSTRAINT ck_index_jobs_index_job_id_type
            CHECK ({_entity_id_check("object_type", "object_id")}),
        CONSTRAINT ck_index_jobs_index_job_attempt_nonnegative CHECK (attempt >= 0),
        CONSTRAINT ck_index_jobs_index_job_revision_positive CHECK (revision >= 1),
        CONSTRAINT uq_index_jobs_object_generation
            UNIQUE (object_type, object_id, generation_id),
        CONSTRAINT ck_index_jobs_index_job_object_type
            CHECK (object_type IN ({_ENTITY_VALUES})),
        CONSTRAINT fk_index_jobs_generation_id_index_generations FOREIGN KEY(generation_id)
            REFERENCES index_generations (id) ON DELETE RESTRICT,
        CONSTRAINT ck_index_jobs_index_job_status CHECK (status IN (
            'PENDING', 'INDEXING', 'INDEXED', 'ACTIVE_INDEXED', 'FAILED',
            'DELETE_PENDING', 'DELETED'
        ))
    )""",
    """CREATE TABLE knowledge_changes (
        id VARCHAR(33) NOT NULL,
        source_id VARCHAR(33) NOT NULL,
        base_version_id VARCHAR(33),
        target_version_id VARCHAR(33) NOT NULL,
        change_type VARCHAR(7) NOT NULL,
        diff_hash VARCHAR(64) NOT NULL,
        diff_summary_json JSON NOT NULL,
        status VARCHAR(14) DEFAULT 'RECEIVED' NOT NULL,
        operation_id VARCHAR(255) NOT NULL,
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_knowledge_changes PRIMARY KEY (id),
        CONSTRAINT ck_knowledge_changes_knowledge_change_id_prefix
            CHECK (id LIKE 'change_%' AND length(id) = 33),
        CONSTRAINT ck_knowledge_changes_knowledge_change_diff_hash
            CHECK (length(diff_hash) = 64 AND diff_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_knowledge_changes_knowledge_change_diff_json_valid
            CHECK (json_valid(diff_summary_json)),
        CONSTRAINT ck_knowledge_changes_knowledge_change_operation_not_empty
            CHECK (length(operation_id) > 0),
        CONSTRAINT ck_knowledge_changes_knowledge_change_revision_positive CHECK (revision >= 1),
        CONSTRAINT fk_knowledge_changes_source_id_sources FOREIGN KEY(source_id)
            REFERENCES sources (id) ON DELETE RESTRICT,
        CONSTRAINT fk_knowledge_changes_base_version_id_source_versions
            FOREIGN KEY(base_version_id) REFERENCES source_versions (id) ON DELETE RESTRICT,
        CONSTRAINT fk_knowledge_changes_target_version_id_source_versions
            FOREIGN KEY(target_version_id) REFERENCES source_versions (id) ON DELETE RESTRICT,
        CONSTRAINT ck_knowledge_changes_change_type
            CHECK (change_type IN ('CREATED', 'UPDATED', 'MOVED', 'DELETED')),
        CONSTRAINT ck_knowledge_changes_knowledge_change_status CHECK (status IN (
            'RECEIVED', 'VALIDATING', 'PUBLISH_INTENT', 'ACTIVE', 'FAILED', 'QUARANTINED'
        ))
    )""",
    """CREATE TABLE knowledge_notes (
        id VARCHAR(31) NOT NULL,
        canonical_path VARCHAR(2048) NOT NULL,
        current_curated_version_id VARCHAR(34),
        active_index_generation_id VARCHAR(33),
        deleted_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_knowledge_notes PRIMARY KEY (id),
        CONSTRAINT ck_knowledge_notes_knowledge_note_id_prefix
            CHECK (id LIKE 'note_%' AND length(id) = 31),
        CONSTRAINT ck_knowledge_notes_knowledge_note_path_not_empty
            CHECK (length(canonical_path) > 0),
        CONSTRAINT ck_knowledge_notes_knowledge_note_revision_positive CHECK (revision >= 1),
        CONSTRAINT fk_knowledge_notes_current_curated_version_id_curated_versions
            FOREIGN KEY(current_curated_version_id) REFERENCES curated_versions (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_knowledge_notes_active_index_generation_id_index_generations
            FOREIGN KEY(active_index_generation_id) REFERENCES index_generations (id)
            ON DELETE RESTRICT
    )""",
    f"""CREATE TABLE lineage_edges (
        id VARCHAR(34) NOT NULL,
        from_type VARCHAR(16) NOT NULL,
        from_id VARCHAR(64) NOT NULL,
        to_type VARCHAR(16) NOT NULL,
        to_id VARCHAR(64) NOT NULL,
        relation VARCHAR(100) NOT NULL,
        operation_id VARCHAR(255) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_lineage_edges PRIMARY KEY (id),
        CONSTRAINT ck_lineage_edges_lineage_edge_id_prefix
            CHECK (id LIKE 'lineage_%' AND length(id) = 34),
        CONSTRAINT ck_lineage_edges_lineage_from_id_type
            CHECK ({_entity_id_check("from_type", "from_id")}),
        CONSTRAINT ck_lineage_edges_lineage_to_id_type
            CHECK ({_entity_id_check("to_type", "to_id")}),
        CONSTRAINT ck_lineage_edges_lineage_relation_not_empty CHECK (length(relation) > 0),
        CONSTRAINT ck_lineage_edges_lineage_operation_not_empty
            CHECK (length(operation_id) > 0),
        CONSTRAINT uq_lineage_edges_relation
            UNIQUE (from_type, from_id, to_type, to_id, relation),
        CONSTRAINT ck_lineage_edges_lineage_from_type CHECK (from_type IN ({_ENTITY_VALUES})),
        CONSTRAINT ck_lineage_edges_lineage_to_type CHECK (to_type IN ({_ENTITY_VALUES}))
    )""",
    """CREATE TABLE model_runs (
        id VARCHAR(35) NOT NULL,
        purpose VARCHAR(21) NOT NULL,
        provider VARCHAR(100) NOT NULL,
        model VARCHAR(255) NOT NULL,
        prompt_version VARCHAR(100) NOT NULL,
        status VARCHAR(9) DEFAULT 'STARTED' NOT NULL,
        input_hash VARCHAR(64) NOT NULL,
        output_hash VARCHAR(64),
        input_tokens INTEGER DEFAULT '0' NOT NULL,
        output_tokens INTEGER DEFAULT '0' NOT NULL,
        total_tokens INTEGER DEFAULT '0' NOT NULL,
        latency_ms INTEGER,
        request_id VARCHAR(255),
        error_category VARCHAR(100),
        started_at VARCHAR(27) NOT NULL,
        completed_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        CONSTRAINT pk_model_runs PRIMARY KEY (id),
        CONSTRAINT ck_model_runs_model_run_id_prefix
            CHECK (id LIKE 'modelrun_%' AND length(id) = 35),
        CONSTRAINT ck_model_runs_model_run_provider_not_empty CHECK (length(provider) > 0),
        CONSTRAINT ck_model_runs_model_run_model_not_empty CHECK (length(model) > 0),
        CONSTRAINT ck_model_runs_model_run_prompt_not_empty CHECK (length(prompt_version) > 0),
        CONSTRAINT ck_model_runs_model_run_input_hash
            CHECK (length(input_hash) = 64 AND input_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_model_runs_model_run_output_hash CHECK (
            output_hash IS NULL
            OR (length(output_hash) = 64 AND output_hash NOT GLOB '*[^0-9a-f]*')
        ),
        CONSTRAINT ck_model_runs_model_run_input_tokens_nonnegative CHECK (input_tokens >= 0),
        CONSTRAINT ck_model_runs_model_run_output_tokens_nonnegative CHECK (output_tokens >= 0),
        CONSTRAINT ck_model_runs_model_run_total_tokens_nonnegative CHECK (total_tokens >= 0),
        CONSTRAINT ck_model_runs_model_run_token_total
            CHECK (total_tokens = input_tokens + output_tokens),
        CONSTRAINT ck_model_runs_model_run_latency_nonnegative
            CHECK (latency_ms IS NULL OR latency_ms >= 0),
        CONSTRAINT ck_model_runs_model_run_time_order
            CHECK (completed_at IS NULL OR completed_at >= started_at),
        CONSTRAINT ck_model_runs_model_run_revision_positive CHECK (revision >= 1),
        CONSTRAINT ck_model_runs_model_run_purpose CHECK (purpose IN (
            'claim_extraction', 'evidence_verification', 'curation', 'answer_generation'
        )),
        CONSTRAINT ck_model_runs_model_run_status
            CHECK (status IN ('STARTED', 'SUCCEEDED', 'FAILED', 'CANCELLED'))
    )""",
    f"""CREATE TABLE operation_logs (
        id VARCHAR(32) NOT NULL,
        operation_id VARCHAR(255) NOT NULL,
        step_number INTEGER NOT NULL,
        actor_type VARCHAR(6) NOT NULL,
        actor_id VARCHAR(255),
        action VARCHAR(255) NOT NULL,
        target_type VARCHAR(16) NOT NULL,
        target_id VARCHAR(64) NOT NULL,
        before_json JSON NOT NULL,
        after_json JSON NOT NULL,
        previous_entry_hash VARCHAR(64),
        entry_hash VARCHAR(64) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_operation_logs PRIMARY KEY (id),
        CONSTRAINT ck_operation_logs_operation_log_id_prefix
            CHECK (id LIKE 'oplog_%' AND length(id) = 32),
        CONSTRAINT ck_operation_logs_operation_log_operation_not_empty
            CHECK (length(operation_id) > 0),
        CONSTRAINT ck_operation_logs_operation_log_step_nonnegative CHECK (step_number >= 0),
        CONSTRAINT ck_operation_logs_operation_log_action_not_empty CHECK (length(action) > 0),
        CONSTRAINT ck_operation_logs_operation_log_target_id_type
            CHECK ({_entity_id_check("target_type", "target_id")}),
        CONSTRAINT ck_operation_logs_operation_log_before_json_valid
            CHECK (json_valid(before_json)),
        CONSTRAINT ck_operation_logs_operation_log_after_json_valid
            CHECK (json_valid(after_json)),
        CONSTRAINT ck_operation_logs_operation_log_previous_hash CHECK (
            previous_entry_hash IS NULL OR (
                length(previous_entry_hash) = 64
                AND previous_entry_hash NOT GLOB '*[^0-9a-f]*'
            )
        ),
        CONSTRAINT ck_operation_logs_operation_log_entry_hash
            CHECK (length(entry_hash) = 64 AND entry_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT uq_operation_logs_step UNIQUE (operation_id, step_number),
        CONSTRAINT ck_operation_logs_operation_log_actor_type
            CHECK (actor_type IN ('USER', 'SYSTEM', 'AGENT')),
        CONSTRAINT ck_operation_logs_operation_log_target_type
            CHECK (target_type IN ({_ENTITY_VALUES})),
        CONSTRAINT uq_operation_logs_entry_hash UNIQUE (entry_hash)
    )""",
    """CREATE TABLE quality_check_evidence (
        quality_check_id VARCHAR(33) NOT NULL,
        evidence_id VARCHAR(35) NOT NULL,
        position INTEGER NOT NULL,
        CONSTRAINT pk_quality_check_evidence PRIMARY KEY (quality_check_id, evidence_id),
        CONSTRAINT ck_quality_check_evidence_quality_evidence_position_nonnegative
            CHECK (position >= 0),
        CONSTRAINT uq_quality_check_evidence_position UNIQUE (quality_check_id, position),
        CONSTRAINT fk_quality_check_evidence_quality_check_id_quality_checks
            FOREIGN KEY(quality_check_id) REFERENCES quality_checks (id) ON DELETE RESTRICT,
        CONSTRAINT fk_quality_check_evidence_evidence_id_evidence FOREIGN KEY(evidence_id)
            REFERENCES evidence (id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE quality_checks (
        id VARCHAR(33) NOT NULL,
        claim_id VARCHAR(32) NOT NULL,
        policy_version VARCHAR(100) NOT NULL,
        verdict VARCHAR(13) NOT NULL,
        dimensions_json JSON NOT NULL,
        reason_code VARCHAR(100) NOT NULL,
        reason_summary VARCHAR(2048) NOT NULL,
        evidence_snapshot_hash VARCHAR(64) NOT NULL,
        model_run_id VARCHAR(35),
        human_override BOOLEAN DEFAULT '0' NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_quality_checks PRIMARY KEY (id),
        CONSTRAINT ck_quality_checks_quality_check_id_prefix
            CHECK (id LIKE 'qcheck_%' AND length(id) = 33),
        CONSTRAINT ck_quality_checks_quality_policy_not_empty CHECK (length(policy_version) > 0),
        CONSTRAINT ck_quality_checks_quality_dimensions_json_valid
            CHECK (json_valid(dimensions_json)),
        CONSTRAINT ck_quality_checks_quality_reason_code_not_empty CHECK (length(reason_code) > 0),
        CONSTRAINT ck_quality_checks_quality_reason_not_empty CHECK (length(reason_summary) > 0),
        CONSTRAINT ck_quality_checks_quality_evidence_snapshot_hash CHECK (
            length(evidence_snapshot_hash) = 64
            AND evidence_snapshot_hash NOT GLOB '*[^0-9a-f]*'
        ),
        CONSTRAINT fk_quality_checks_claim_id_claims FOREIGN KEY(claim_id)
            REFERENCES claims (id) ON DELETE RESTRICT,
        CONSTRAINT ck_quality_checks_quality_verdict CHECK (verdict IN (
            'VERIFIED', 'USER_ASSERTED', 'OPINION', 'INSUFFICIENT', 'CONTESTED',
            'REJECTED', 'QUARANTINED'
        )),
        CONSTRAINT fk_quality_checks_model_run_id_model_runs FOREIGN KEY(model_run_id)
            REFERENCES model_runs (id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE source_versions (
        id VARCHAR(33) NOT NULL,
        source_id VARCHAR(33) NOT NULL,
        version_number INTEGER NOT NULL,
        content_hash VARCHAR(64) NOT NULL,
        byte_size INTEGER NOT NULL,
        media_type VARCHAR(255) NOT NULL,
        captured_at VARCHAR(27) NOT NULL,
        source_modified_at VARCHAR(27),
        original_path VARCHAR(2048) NOT NULL,
        status VARCHAR(12) DEFAULT 'CAPTURED' NOT NULL,
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_source_versions PRIMARY KEY (id),
        CONSTRAINT ck_source_versions_source_version_id_prefix
            CHECK (id LIKE 'srcver_%' AND length(id) = 33),
        CONSTRAINT ck_source_versions_source_version_number_positive CHECK (version_number >= 1),
        CONSTRAINT ck_source_versions_source_version_content_hash
            CHECK (length(content_hash) = 64 AND content_hash NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_source_versions_source_version_byte_size_nonnegative CHECK (byte_size >= 0),
        CONSTRAINT ck_source_versions_source_version_media_type_not_empty
            CHECK (length(media_type) > 0),
        CONSTRAINT ck_source_versions_source_version_path_not_empty
            CHECK (length(original_path) > 0),
        CONSTRAINT ck_source_versions_source_version_revision_positive CHECK (revision >= 1),
        CONSTRAINT uq_source_versions_number UNIQUE (source_id, version_number),
        CONSTRAINT uq_source_versions_content UNIQUE (source_id, content_hash),
        CONSTRAINT fk_source_versions_source_id_sources FOREIGN KEY(source_id)
            REFERENCES sources (id) ON DELETE RESTRICT,
        CONSTRAINT ck_source_versions_source_version_status CHECK (status IN (
            'CAPTURED', 'PARSED', 'READY', 'PARSE_FAILED', 'QUARANTINED', 'DELETED'
        ))
    )""",
    """CREATE TABLE sources (
        id VARCHAR(33) NOT NULL,
        source_type VARCHAR(22) NOT NULL,
        canonical_uri VARCHAR(2048) NOT NULL,
        owner VARCHAR(255) NOT NULL,
        trust_tier VARCHAR(2) NOT NULL,
        sensitivity VARCHAR(10) NOT NULL,
        current_version_id VARCHAR(33),
        deleted_at VARCHAR(27),
        revision INTEGER DEFAULT 1 NOT NULL,
        updated_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        created_at VARCHAR(27) DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')) NOT NULL,
        CONSTRAINT pk_sources PRIMARY KEY (id),
        CONSTRAINT ck_sources_source_id_prefix
            CHECK (id LIKE 'source_%' AND length(id) = 33),
        CONSTRAINT ck_sources_source_uri_not_empty CHECK (length(canonical_uri) > 0),
        CONSTRAINT ck_sources_source_owner_not_empty CHECK (length(owner) > 0),
        CONSTRAINT ck_sources_source_revision_positive CHECK (revision >= 1),
        CONSTRAINT ck_sources_source_type CHECK (source_type IN (
            'obsidian_markdown', 'web_page', 'official_documentation', 'source_code',
            'local_file', 'user_input'
        )),
        CONSTRAINT ck_sources_trust_tier CHECK (trust_tier IN ('T0', 'T1', 'T2', 'T3', 'T4', 'T5')),
        CONSTRAINT ck_sources_sensitivity
            CHECK (sensitivity IN ('private', 'restricted', 'public')),
        CONSTRAINT fk_sources_current_version_id_source_versions FOREIGN KEY(current_version_id)
            REFERENCES source_versions (id) ON DELETE RESTRICT
    )""",
)

_INDEX_DDL = (
    "CREATE INDEX ix_content_blocks_source_version_id ON content_blocks (source_version_id)",
    "CREATE INDEX ix_curated_versions_note_id ON curated_versions (note_id)",
    "CREATE INDEX ix_evidence_claim_id ON evidence (claim_id)",
    "CREATE INDEX ix_index_jobs_object_id ON index_jobs (object_id)",
    "CREATE INDEX ix_knowledge_changes_operation_id ON knowledge_changes (operation_id)",
    "CREATE INDEX ix_knowledge_changes_source_id ON knowledge_changes (source_id)",
    "CREATE INDEX ix_lineage_edges_from_id ON lineage_edges (from_id)",
    "CREATE INDEX ix_lineage_edges_operation_id ON lineage_edges (operation_id)",
    "CREATE INDEX ix_lineage_edges_to_id ON lineage_edges (to_id)",
    "CREATE INDEX ix_operation_logs_operation_id ON operation_logs (operation_id)",
    "CREATE INDEX ix_operation_logs_target ON operation_logs (target_type, target_id)",
    "CREATE INDEX ix_operation_logs_target_id ON operation_logs (target_id)",
    "CREATE INDEX ix_quality_checks_claim_id ON quality_checks (claim_id)",
    "CREATE INDEX ix_source_versions_source_id ON source_versions (source_id)",
    """CREATE UNIQUE INDEX uq_index_generations_one_active
        ON index_generations (status) WHERE status = 'ACTIVE'""",
    """CREATE UNIQUE INDEX uq_knowledge_notes_live_path
        ON knowledge_notes (canonical_path) WHERE deleted_at IS NULL""",
    """CREATE UNIQUE INDEX uq_sources_live_identity
        ON sources (source_type, canonical_uri, owner) WHERE deleted_at IS NULL""",
)

_TRIGGER_DDL = (
    """CREATE TRIGGER trg_operation_logs_no_update
        BEFORE UPDATE ON operation_logs
        BEGIN
            SELECT RAISE(ABORT, 'operation_logs are append-only');
        END""",
    """CREATE TRIGGER trg_operation_logs_no_delete
        BEFORE DELETE ON operation_logs
        BEGIN
            SELECT RAISE(ABORT, 'operation_logs are append-only');
        END""",
)

_DROP_TABLES = tuple(
    reversed(
        (
            "claim_origins",
            "claims",
            "content_blocks",
            "curated_versions",
            "evidence",
            "evidence_families",
            "idempotency_records",
            "index_generations",
            "index_jobs",
            "knowledge_changes",
            "knowledge_notes",
            "lineage_edges",
            "model_runs",
            "operation_logs",
            "quality_check_evidence",
            "quality_checks",
            "source_versions",
            "sources",
        )
    )
)


def upgrade() -> None:
    for statement in (*_TABLE_DDL, *_INDEX_DDL, *_TRIGGER_DDL):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_operation_logs_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_operation_logs_no_update")
    for table_name in _DROP_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table_name}"')
