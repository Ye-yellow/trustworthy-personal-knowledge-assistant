"""Stable string enums for the L1 control plane."""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    OBSIDIAN_MARKDOWN = "obsidian_markdown"
    WEB_PAGE = "web_page"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    SOURCE_CODE = "source_code"
    LOCAL_FILE = "local_file"
    USER_INPUT = "user_input"


class TrustTier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    T5 = "T5"


class Sensitivity(StrEnum):
    PRIVATE = "private"
    RESTRICTED = "restricted"
    PUBLIC = "public"


class SourceVersionStatus(StrEnum):
    CAPTURED = "CAPTURED"
    PARSED = "PARSED"
    READY = "READY"
    PARSE_FAILED = "PARSE_FAILED"
    QUARANTINED = "QUARANTINED"
    DELETED = "DELETED"


class ClaimType(StrEnum):
    FACT = "FACT"
    DEFINITION = "DEFINITION"
    PROCEDURE = "PROCEDURE"
    USER_EXPERIENCE = "USER_EXPERIENCE"
    PREFERENCE = "PREFERENCE"
    DECISION = "DECISION"
    PREDICTION = "PREDICTION"
    CODE_BEHAVIOR = "CODE_BEHAVIOR"
    OPINION = "OPINION"


class ClaimStatus(StrEnum):
    PROPOSED = "PROPOSED"
    VERIFIED = "VERIFIED"
    USER_ASSERTED = "USER_ASSERTED"
    OPINION = "OPINION"
    INSUFFICIENT = "INSUFFICIENT"
    CONTESTED = "CONTESTED"
    OUTDATED = "OUTDATED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    QUARANTINED = "QUARANTINED"


class EvidenceStance(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    NEUTRAL = "NEUTRAL"


class QualityVerdict(StrEnum):
    VERIFIED = "VERIFIED"
    USER_ASSERTED = "USER_ASSERTED"
    OPINION = "OPINION"
    INSUFFICIENT = "INSUFFICIENT"
    CONTESTED = "CONTESTED"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"


class CuratedVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    STAGING = "STAGING"
    ACTIVE = "ACTIVE"
    STALE_PENDING_REVIEW = "STALE_PENDING_REVIEW"
    SUPERSEDED = "SUPERSEDED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


class IndexGenerationStatus(StrEnum):
    STAGING = "STAGING"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


class IndexJobStatus(StrEnum):
    PENDING = "PENDING"
    INDEXING = "INDEXING"
    INDEXED = "INDEXED"
    ACTIVE_INDEXED = "ACTIVE_INDEXED"
    FAILED = "FAILED"
    DELETE_PENDING = "DELETE_PENDING"
    DELETED = "DELETED"


class PublicationRunStatus(StrEnum):
    PLANNING = "PLANNING"
    CURATING = "CURATING"
    VAULT_STAGED = "VAULT_STAGED"
    INDEXING = "INDEXING"
    INDEX_VERIFIED = "INDEX_VERIFIED"
    VAULT_PUBLISHED = "VAULT_PUBLISHED"
    ACTIVATING = "ACTIVATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AnswerScope(StrEnum):
    AUTO = "auto"
    GENERAL = "general"
    PERSONAL = "personal"


class AnswerRunStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    ANSWERED = "ANSWERED"
    REFUSED = "REFUSED"
    FAILED = "FAILED"


class ChangeType(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    MOVED = "MOVED"
    DELETED = "DELETED"


class KnowledgeChangeStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    PUBLISH_INTENT = "PUBLISH_INTENT"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ModelRunPurpose(StrEnum):
    CLAIM_EXTRACTION = "claim_extraction"
    EVIDENCE_VERIFICATION = "evidence_verification"
    CURATION = "curation"
    ANSWER_GENERATION = "answer_generation"
    EVIDENCE_SEARCH = "evidence_search"


class GovernanceRunStatus(StrEnum):
    PLANNING = "PLANNING"
    EXTRACTING = "EXTRACTING"
    EVALUATING = "EVALUATING"
    RECONCILING = "RECONCILING"
    COMPLETED = "COMPLETED"
    PARTIAL_FAILED = "PARTIAL_FAILED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


class GovernanceItemStage(StrEnum):
    EXTRACTED = "EXTRACTED"
    EVIDENCE_PENDING = "EVIDENCE_PENDING"
    VERIFYING = "VERIFYING"
    DECIDING = "DECIDING"
    DECIDED = "DECIDED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"


class ReviewRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ModelRunStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class IngestionAction(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    MOVED = "MOVED"
    DELETED = "DELETED"
    UNCHANGED = "UNCHANGED"


class IngestionRunStatus(StrEnum):
    PLANNING = "PLANNING"
    APPLYING = "APPLYING"
    COMPLETED = "COMPLETED"
    PARTIAL_FAILED = "PARTIAL_FAILED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class IngestionItemStatus(StrEnum):
    PENDING = "PENDING"
    APPLYING = "APPLYING"
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


class EntityType(StrEnum):
    SOURCE = "source"
    SOURCE_VERSION = "source_version"
    CONTENT_BLOCK = "content_block"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    QUALITY_CHECK = "quality_check"
    KNOWLEDGE_CHANGE = "knowledge_change"
    KNOWLEDGE_NOTE = "knowledge_note"
    CURATED_VERSION = "curated_version"
    INDEX_GENERATION = "index_generation"
    INDEX_JOB = "index_job"
    MODEL_RUN = "model_run"


class ActorType(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"


__all__ = [
    "ActorType",
    "AnswerRunStatus",
    "AnswerScope",
    "ChangeType",
    "ClaimStatus",
    "ClaimType",
    "CuratedVersionStatus",
    "EntityType",
    "EvidenceStance",
    "GovernanceItemStage",
    "GovernanceRunStatus",
    "IdempotencyStatus",
    "IndexGenerationStatus",
    "IndexJobStatus",
    "IngestionAction",
    "IngestionItemStatus",
    "IngestionRunStatus",
    "KnowledgeChangeStatus",
    "ModelRunPurpose",
    "ModelRunStatus",
    "PublicationRunStatus",
    "QualityVerdict",
    "ReviewRequestStatus",
    "RiskLevel",
    "Sensitivity",
    "SourceType",
    "SourceVersionStatus",
    "TrustTier",
]
