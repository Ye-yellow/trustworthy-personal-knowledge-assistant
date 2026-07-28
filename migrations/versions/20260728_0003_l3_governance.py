"""Create L3 claim, evidence, quality-governance state.

Revision ID: 20260728_0003
Revises: 20260728_0002
Create Date: 2026-07-28
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

from trustworthy_kb.persistence.knowledge_tables import ClaimTable
from trustworthy_kb.persistence.publication_tables import KnowledgeChangeTable

revision: str = "20260728_0003"
down_revision: str | None = "20260728_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHA_CHECK = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"
_UTC_DEFAULT = sa.text("(strftime('%Y-%m-%dT%H:%M:%f000Z','now'))")


def _governance_tables() -> None:
    op.create_table(
        "governance_runs",
        sa.Column("id", sa.String(33), nullable=False),
        sa.Column("knowledge_change_id", sa.String(33), nullable=False),
        sa.Column("target_source_version_id", sa.String(33), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("extractor_version", sa.String(100), nullable=False),
        sa.Column("verifier_version", sa.String(100), nullable=False),
        sa.Column("search_policy_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(14), server_default="PLANNING", nullable=False),
        sa.Column("total_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("decided_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("review_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("quarantined_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_category", sa.String(100), nullable=True),
        sa.Column("started_at", sa.String(27), server_default=_UTC_DEFAULT, nullable=False),
        sa.Column("completed_at", sa.String(27), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_at", sa.String(27), server_default=_UTC_DEFAULT, nullable=False),
        sa.Column("created_at", sa.String(27), server_default=_UTC_DEFAULT, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_governance_runs"),
        sa.ForeignKeyConstraint(
            ["knowledge_change_id"],
            ["knowledge_changes.id"],
            name="fk_governance_runs_knowledge_change_id_knowledge_changes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_source_version_id"],
            ["source_versions.id"],
            name="fk_governance_runs_target_source_version_id_source_versions",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "knowledge_change_id", "policy_version", name="uq_governance_runs_change_policy"
        ),
        sa.CheckConstraint(
            "id LIKE 'govrun_%' AND length(id) = 33",
            name=op.f("ck_governance_runs_governance_run_id_prefix"),
        ),
        sa.CheckConstraint(
            "length(policy_version) > 0",
            name=op.f("ck_governance_runs_governance_policy_not_empty"),
        ),
        sa.CheckConstraint(
            "length(extractor_version) > 0",
            name=op.f("ck_governance_runs_governance_extractor_not_empty"),
        ),
        sa.CheckConstraint(
            "length(verifier_version) > 0",
            name=op.f("ck_governance_runs_governance_verifier_not_empty"),
        ),
        sa.CheckConstraint(
            "length(search_policy_version) > 0",
            name=op.f("ck_governance_runs_governance_search_policy_not_empty"),
        ),
        sa.CheckConstraint(
            "status IN ('PLANNING','EXTRACTING','EVALUATING','RECONCILING','COMPLETED',"
            "'PARTIAL_FAILED','FAILED','QUARANTINED')",
            name=op.f("ck_governance_runs_governance_run_status"),
        ),
        sa.CheckConstraint(
            "total_items >= 0 AND decided_items >= 0 AND review_items >= 0 "
            "AND failed_items >= 0 AND quarantined_items >= 0",
            name=op.f("ck_governance_runs_governance_run_counts_nonnegative"),
        ),
        sa.CheckConstraint(
            "decided_items + review_items + failed_items + quarantined_items <= total_items",
            name=op.f("ck_governance_runs_governance_run_counts_bounded"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name=op.f("ck_governance_runs_governance_run_completed_after_start"),
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name=op.f("ck_governance_runs_governance_run_revision_positive"),
        ),
    )
    op.create_index("ix_governance_runs_change_id", "governance_runs", ["knowledge_change_id"])

    op.create_table(
        "governance_items",
        sa.Column("id", sa.String(34), nullable=False),
        sa.Column("run_id", sa.String(33), nullable=False),
        sa.Column("claim_id", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(16), server_default="EXTRACTED", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("risk_level", sa.String(6), nullable=False),
        sa.Column("search_manifest_hash", sa.String(64), nullable=True),
        sa.Column("evidence_pack_hash", sa.String(64), nullable=True),
        sa.Column("current_quality_check_id", sa.String(33), nullable=True),
        sa.Column("error_category", sa.String(100), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_at", sa.String(27), server_default=_UTC_DEFAULT, nullable=False),
        sa.Column("created_at", sa.String(27), server_default=_UTC_DEFAULT, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_governance_items"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["governance_runs.id"],
            name="fk_governance_items_run_id_governance_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name="fk_governance_items_claim_id_claims",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["current_quality_check_id"],
            ["quality_checks.id"],
            name="fk_governance_items_current_quality_check_id_quality_checks",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("run_id", "claim_id", name="uq_governance_items_run_claim"),
        sa.CheckConstraint(
            "id LIKE 'govitem_%' AND length(id) = 34",
            name=op.f("ck_governance_items_governance_item_id_prefix"),
        ),
        sa.CheckConstraint(
            "stage IN ('EXTRACTED','EVIDENCE_PENDING','VERIFYING','DECIDING','DECIDED',"
            "'REVIEW_REQUIRED','FAILED')",
            name=op.f("ck_governance_items_governance_item_stage"),
        ),
        sa.CheckConstraint(
            "risk_level IN ('LOW','MEDIUM','HIGH')",
            name=op.f("ck_governance_items_governance_risk_level"),
        ),
        sa.CheckConstraint(
            "attempt >= 1",
            name=op.f("ck_governance_items_governance_item_attempt_positive"),
        ),
        sa.CheckConstraint(
            "search_manifest_hash IS NULL OR (" + _SHA_CHECK.format("search_manifest_hash") + ")",
            name=op.f("ck_governance_items_governance_item_search_manifest_hash"),
        ),
        sa.CheckConstraint(
            "evidence_pack_hash IS NULL OR (" + _SHA_CHECK.format("evidence_pack_hash") + ")",
            name=op.f("ck_governance_items_governance_item_evidence_pack_hash"),
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name=op.f("ck_governance_items_governance_item_revision_positive"),
        ),
    )
    op.create_index("ix_governance_items_run_stage", "governance_items", ["run_id", "stage"])

    op.create_table(
        "review_requests",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("claim_id", sa.String(32), nullable=False),
        sa.Column("quality_check_id", sa.String(33), nullable=False),
        sa.Column("knowledge_change_id", sa.String(33), nullable=False),
        sa.Column("risk_level", sa.String(6), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("status", sa.String(9), server_default="PENDING", nullable=False),
        sa.Column("decision_reason_code", sa.String(100), nullable=True),
        sa.Column("decision_actor_type", sa.String(6), nullable=True),
        sa.Column("decided_at", sa.String(27), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_at", sa.String(27), server_default=_UTC_DEFAULT, nullable=False),
        sa.Column("created_at", sa.String(27), server_default=_UTC_DEFAULT, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_review_requests"),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name="fk_review_requests_claim_id_claims",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quality_check_id"],
            ["quality_checks.id"],
            name="fk_review_requests_quality_check_id_quality_checks",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_change_id"],
            ["knowledge_changes.id"],
            name="fk_review_requests_knowledge_change_id_knowledge_changes",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "id LIKE 'reviewreq_%' AND length(id) = 36",
            name=op.f("ck_review_requests_review_request_id_prefix"),
        ),
        sa.CheckConstraint(
            "risk_level IN ('LOW','MEDIUM','HIGH')",
            name=op.f("ck_review_requests_review_risk_level"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','CANCELLED')",
            name=op.f("ck_review_requests_review_request_status"),
        ),
        sa.CheckConstraint(
            "decision_actor_type IS NULL OR decision_actor_type IN ('USER','SYSTEM','AGENT')",
            name=op.f("ck_review_requests_review_decision_actor_type"),
        ),
        sa.CheckConstraint(
            "length(reason_code) > 0",
            name=op.f("ck_review_requests_review_reason_not_empty"),
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND decision_reason_code IS NULL AND "
            "decision_actor_type IS NULL AND decided_at IS NULL) OR "
            "(status <> 'PENDING' AND decision_reason_code IS NOT NULL AND "
            "decision_actor_type IS NOT NULL AND decided_at IS NOT NULL)",
            name=op.f("ck_review_requests_review_decision_consistent"),
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name=op.f("ck_review_requests_review_request_revision_positive"),
        ),
    )
    op.create_index("ix_review_requests_status", "review_requests", ["status"])
    op.create_index(
        "uq_review_requests_live_quality_check",
        "review_requests",
        ["quality_check_id"],
        unique=True,
        sqlite_where=sa.text("status = 'PENDING'"),
    )


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
        ).encode("utf-8")
    ).hexdigest()


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _backfill_claim_hashes() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, claim_type, subject, predicate, object_json, scope_json, "
            "valid_from, valid_to, freshness_at, sensitivity FROM claims"
        )
    ).mappings()
    for row in rows:
        scope: dict[str, Any] = json.loads(row["scope_json"])
        exact = {
            "claim_type": row["claim_type"],
            "subject": row["subject"],
            "predicate": row["predicate"],
            "scope": scope,
            "object": json.loads(row["object_json"]),
            "valid_from": _timestamp(row["valid_from"]),
            "valid_to": _timestamp(row["valid_to"]),
            "freshness_at": _timestamp(row["freshness_at"]),
            "sensitivity": row["sensitivity"],
        }
        family = {
            "claim_type": row["claim_type"],
            "subject": row["subject"],
            "predicate": row["predicate"],
            "scope": {key: value for key, value in scope.items() if key != "lifecycle_status"},
        }
        connection.execute(
            sa.text(
                "UPDATE claims SET claim_fingerprint=:fingerprint, claim_family_key=:family "
                "WHERE id=:id"
            ),
            {
                "id": row["id"],
                "fingerprint": _canonical_hash(exact),
                "family": _canonical_hash(family),
            },
        )
    duplicate = connection.execute(
        sa.text(
            "SELECT claim_fingerprint FROM claims WHERE deleted_at IS NULL AND status NOT IN "
            "('OUTDATED','REJECTED','SUPERSEDED','QUARANTINED') "
            "GROUP BY claim_fingerprint HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError("active claim fingerprint conflict; repair data before migration")


def _plain_enum_copy(
    source: sa.Table,
    enum_columns: dict[str, int],
    enum_checks: tuple[tuple[str, str], ...],
) -> sa.Table:
    table = source.to_metadata(sa.MetaData())
    for column_name, length in enum_columns.items():
        table.c[column_name].type = sa.String(length)
    for constraint in tuple(table.constraints):
        if isinstance(constraint, sa.CheckConstraint) and getattr(constraint, "_type_bound", False):
            table.constraints.remove(constraint)
    for name, expression in enum_checks:
        table.append_constraint(sa.CheckConstraint(expression, name=name))
    return table


def _upgrade_existing_tables() -> None:
    op.add_column("claims", sa.Column("claim_fingerprint", sa.String(64), nullable=True))
    op.add_column("claims", sa.Column("claim_family_key", sa.String(64), nullable=True))
    _backfill_claim_hashes()
    claim_copy = _plain_enum_copy(
        ClaimTable.__table__,
        {"claim_type": 15, "sensitivity": 10, "status": 14},
        (
            (
                "ck_claims_claim_type",
                "claim_type IN ('FACT','DEFINITION','PROCEDURE','USER_EXPERIENCE',"
                "'PREFERENCE','DECISION','PREDICTION','CODE_BEHAVIOR','OPINION')",
            ),
            (
                "ck_claims_claim_sensitivity",
                "sensitivity IN ('private','restricted','public')",
            ),
            (
                "ck_claims_claim_status",
                "status IN ('PROPOSED','VERIFIED','USER_ASSERTED','OPINION','INSUFFICIENT',"
                "'CONTESTED','OUTDATED','REJECTED','SUPERSEDED','QUARANTINED')",
            ),
        ),
    )
    with op.batch_alter_table("claims", recreate="always", copy_from=claim_copy):
        pass
    change_copy = _plain_enum_copy(
        KnowledgeChangeTable.__table__,
        {"change_type": 7, "status": 15},
        (
            (
                "ck_knowledge_changes_change_type",
                "change_type IN ('CREATED','UPDATED','MOVED','DELETED')",
            ),
            (
                "ck_knowledge_changes_knowledge_change_status",
                "status IN ('RECEIVED','VALIDATING','PUBLISH_INTENT','ACTIVE','FAILED',"
                "'QUARANTINED','REVIEW_REQUIRED')",
            ),
        ),
    )
    with op.batch_alter_table("knowledge_changes", recreate="always", copy_from=change_copy):
        pass
    op.create_index("ix_knowledge_changes_source_id", "knowledge_changes", ["source_id"])
    op.create_index("ix_knowledge_changes_operation_id", "knowledge_changes", ["operation_id"])
    with op.batch_alter_table("model_runs", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_model_runs_model_run_purpose"), type_="check")
        batch.create_check_constraint(
            "model_run_purpose",
            "purpose IN ('claim_extraction','evidence_verification','curation',"
            "'answer_generation','evidence_search')",
        )


def _offline_existing_tables() -> None:
    zero = "0" * 64
    op.add_column(
        "claims",
        sa.Column("claim_fingerprint", sa.String(64), server_default=zero, nullable=False),
    )
    op.add_column(
        "claims", sa.Column("claim_family_key", sa.String(64), server_default=zero, nullable=False)
    )
    op.create_index("ix_claims_claim_family_key", "claims", ["claim_family_key"])
    op.create_index(
        "uq_claims_active_fingerprint",
        "claims",
        ["claim_fingerprint"],
        unique=True,
        sqlite_where=sa.text(
            "deleted_at IS NULL AND status NOT IN "
            "('OUTDATED','REJECTED','SUPERSEDED','QUARANTINED')"
        ),
    )


def upgrade() -> None:
    if op.get_context().as_sql:
        _offline_existing_tables()
    else:
        _upgrade_existing_tables()
    _governance_tables()


def downgrade() -> None:
    for table in ("review_requests", "governance_items", "governance_runs"):
        op.drop_table(table)
    if op.get_context().as_sql:
        op.drop_index("uq_claims_active_fingerprint", table_name="claims")
        op.drop_index("ix_claims_claim_family_key", table_name="claims")
        op.drop_column("claims", "claim_family_key")
        op.drop_column("claims", "claim_fingerprint")
        return
    with op.batch_alter_table("model_runs", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_model_runs_model_run_purpose"), type_="check")
        batch.create_check_constraint(
            "model_run_purpose",
            "purpose IN ('claim_extraction','evidence_verification','curation',"
            "'answer_generation')",
        )
    with op.batch_alter_table("knowledge_changes", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_knowledge_changes_knowledge_change_status"), type_="check")
        batch.create_check_constraint(
            "knowledge_change_status",
            "status IN ('RECEIVED','VALIDATING','PUBLISH_INTENT','ACTIVE','FAILED','QUARANTINED')",
        )
    op.drop_index("uq_claims_active_fingerprint", table_name="claims")
    op.drop_index("ix_claims_claim_family_key", table_name="claims")
    with op.batch_alter_table("claims", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_claims_claim_fingerprint"), type_="check")
        batch.drop_constraint(op.f("ck_claims_claim_family_key"), type_="check")
        batch.drop_constraint(op.f("ck_claims_claim_type"), type_="check")
        batch.drop_constraint(op.f("ck_claims_claim_status"), type_="check")
        batch.create_check_constraint(
            "claim_type",
            "claim_type IN ('FACT','DEFINITION','PROCEDURE','USER_EXPERIENCE','PREFERENCE',"
            "'DECISION','PREDICTION','CODE_BEHAVIOR')",
        )
        batch.create_check_constraint(
            "claim_status",
            "status IN ('PROPOSED','VERIFIED','USER_ASSERTED','OPINION','INSUFFICIENT',"
            "'CONTESTED','OUTDATED','REJECTED','SUPERSEDED')",
        )
        batch.drop_column("claim_family_key")
        batch.drop_column("claim_fingerprint")
