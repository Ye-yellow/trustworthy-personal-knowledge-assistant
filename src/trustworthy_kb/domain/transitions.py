"""Pure, fail-closed state-transition rules."""

from __future__ import annotations

from enum import StrEnum

from trustworthy_kb.domain.enums import (
    AnswerRunStatus,
    ClaimStatus,
    CuratedVersionStatus,
    GovernanceItemStage,
    GovernanceRunStatus,
    IdempotencyStatus,
    IndexGenerationStatus,
    IndexJobStatus,
    IngestionItemStatus,
    IngestionRunStatus,
    KnowledgeChangeStatus,
    ModelRunStatus,
    PublicationRunStatus,
    ReviewRequestStatus,
    SourceVersionStatus,
)
from trustworthy_kb.domain.errors import InvalidStateTransitionError

_TRANSITIONS: dict[type[StrEnum], dict[StrEnum, frozenset[StrEnum]]] = {
    AnswerRunStatus: {
        AnswerRunStatus.IN_PROGRESS: frozenset(
            {
                AnswerRunStatus.ANSWERED,
                AnswerRunStatus.REFUSED,
                AnswerRunStatus.FAILED,
            }
        )
    },
    SourceVersionStatus: {
        SourceVersionStatus.CAPTURED: frozenset(
            {
                SourceVersionStatus.PARSED,
                SourceVersionStatus.PARSE_FAILED,
                SourceVersionStatus.QUARANTINED,
                SourceVersionStatus.DELETED,
            }
        ),
        SourceVersionStatus.PARSED: frozenset(
            {
                SourceVersionStatus.READY,
                SourceVersionStatus.PARSE_FAILED,
                SourceVersionStatus.QUARANTINED,
                SourceVersionStatus.DELETED,
            }
        ),
        SourceVersionStatus.READY: frozenset({SourceVersionStatus.DELETED}),
        SourceVersionStatus.PARSE_FAILED: frozenset(
            {
                SourceVersionStatus.PARSED,
                SourceVersionStatus.QUARANTINED,
                SourceVersionStatus.DELETED,
            }
        ),
        SourceVersionStatus.QUARANTINED: frozenset({SourceVersionStatus.DELETED}),
    },
    ClaimStatus: {
        ClaimStatus.PROPOSED: frozenset(
            {
                ClaimStatus.VERIFIED,
                ClaimStatus.USER_ASSERTED,
                ClaimStatus.OPINION,
                ClaimStatus.INSUFFICIENT,
                ClaimStatus.CONTESTED,
                ClaimStatus.REJECTED,
                ClaimStatus.QUARANTINED,
            }
        ),
        **{
            state: frozenset({ClaimStatus.OUTDATED, ClaimStatus.SUPERSEDED})
            for state in (
                ClaimStatus.VERIFIED,
                ClaimStatus.USER_ASSERTED,
                ClaimStatus.OPINION,
                ClaimStatus.INSUFFICIENT,
                ClaimStatus.CONTESTED,
            )
        },
    },
    CuratedVersionStatus: {
        CuratedVersionStatus.DRAFT: frozenset({CuratedVersionStatus.VALIDATING}),
        CuratedVersionStatus.VALIDATING: frozenset(
            {
                CuratedVersionStatus.STAGING,
                CuratedVersionStatus.QUARANTINED,
                CuratedVersionStatus.FAILED,
            }
        ),
        CuratedVersionStatus.STAGING: frozenset(
            {
                CuratedVersionStatus.ACTIVE,
                CuratedVersionStatus.QUARANTINED,
                CuratedVersionStatus.FAILED,
            }
        ),
        CuratedVersionStatus.ACTIVE: frozenset(
            {
                CuratedVersionStatus.STALE_PENDING_REVIEW,
                CuratedVersionStatus.SUPERSEDED,
            }
        ),
        CuratedVersionStatus.STALE_PENDING_REVIEW: frozenset({CuratedVersionStatus.SUPERSEDED}),
    },
    IndexGenerationStatus: {
        IndexGenerationStatus.STAGING: frozenset(
            {IndexGenerationStatus.ACTIVE, IndexGenerationStatus.FAILED}
        ),
        IndexGenerationStatus.ACTIVE: frozenset({IndexGenerationStatus.SUPERSEDED}),
    },
    IndexJobStatus: {
        IndexJobStatus.PENDING: frozenset({IndexJobStatus.INDEXING}),
        IndexJobStatus.INDEXING: frozenset({IndexJobStatus.INDEXED, IndexJobStatus.FAILED}),
        IndexJobStatus.INDEXED: frozenset({IndexJobStatus.ACTIVE_INDEXED}),
        IndexJobStatus.ACTIVE_INDEXED: frozenset({IndexJobStatus.DELETE_PENDING}),
        IndexJobStatus.DELETE_PENDING: frozenset({IndexJobStatus.DELETED}),
        IndexJobStatus.FAILED: frozenset({IndexJobStatus.PENDING}),
    },
    KnowledgeChangeStatus: {
        KnowledgeChangeStatus.RECEIVED: frozenset(
            {
                KnowledgeChangeStatus.VALIDATING,
                KnowledgeChangeStatus.FAILED,
                KnowledgeChangeStatus.QUARANTINED,
            }
        ),
        KnowledgeChangeStatus.VALIDATING: frozenset(
            {
                KnowledgeChangeStatus.PUBLISH_INTENT,
                KnowledgeChangeStatus.REVIEW_REQUIRED,
                KnowledgeChangeStatus.FAILED,
                KnowledgeChangeStatus.QUARANTINED,
            }
        ),
        KnowledgeChangeStatus.REVIEW_REQUIRED: frozenset(
            {
                KnowledgeChangeStatus.PUBLISH_INTENT,
                KnowledgeChangeStatus.FAILED,
                KnowledgeChangeStatus.QUARANTINED,
            }
        ),
        KnowledgeChangeStatus.PUBLISH_INTENT: frozenset(
            {
                KnowledgeChangeStatus.ACTIVE,
                KnowledgeChangeStatus.FAILED,
                KnowledgeChangeStatus.QUARANTINED,
            }
        ),
    },
    ModelRunStatus: {
        ModelRunStatus.STARTED: frozenset(
            {ModelRunStatus.SUCCEEDED, ModelRunStatus.FAILED, ModelRunStatus.CANCELLED}
        )
    },
    IdempotencyStatus: {
        IdempotencyStatus.IN_PROGRESS: frozenset(
            {
                IdempotencyStatus.SUCCEEDED,
                IdempotencyStatus.FAILED,
                IdempotencyStatus.UNKNOWN,
            }
        ),
        IdempotencyStatus.UNKNOWN: frozenset(
            {IdempotencyStatus.SUCCEEDED, IdempotencyStatus.FAILED}
        ),
    },
    IngestionRunStatus: {
        IngestionRunStatus.PLANNING: frozenset(
            {
                IngestionRunStatus.APPLYING,
                IngestionRunStatus.FAILED,
                IngestionRunStatus.ABANDONED,
            }
        ),
        IngestionRunStatus.APPLYING: frozenset(
            {
                IngestionRunStatus.COMPLETED,
                IngestionRunStatus.PARTIAL_FAILED,
                IngestionRunStatus.FAILED,
                IngestionRunStatus.ABANDONED,
            }
        ),
    },
    IngestionItemStatus: {
        IngestionItemStatus.PENDING: frozenset({IngestionItemStatus.APPLYING}),
        IngestionItemStatus.APPLYING: frozenset(
            {
                IngestionItemStatus.SUCCEEDED,
                IngestionItemStatus.SKIPPED,
                IngestionItemStatus.QUARANTINED,
                IngestionItemStatus.FAILED,
            }
        ),
        IngestionItemStatus.FAILED: frozenset({IngestionItemStatus.PENDING}),
    },
    GovernanceRunStatus: {
        GovernanceRunStatus.PLANNING: frozenset(
            {
                GovernanceRunStatus.EXTRACTING,
                GovernanceRunStatus.FAILED,
                GovernanceRunStatus.QUARANTINED,
            }
        ),
        GovernanceRunStatus.EXTRACTING: frozenset(
            {
                GovernanceRunStatus.EVALUATING,
                GovernanceRunStatus.FAILED,
                GovernanceRunStatus.QUARANTINED,
            }
        ),
        GovernanceRunStatus.EVALUATING: frozenset(
            {
                GovernanceRunStatus.RECONCILING,
                GovernanceRunStatus.FAILED,
                GovernanceRunStatus.QUARANTINED,
            }
        ),
        GovernanceRunStatus.RECONCILING: frozenset(
            {
                GovernanceRunStatus.COMPLETED,
                GovernanceRunStatus.PARTIAL_FAILED,
                GovernanceRunStatus.FAILED,
                GovernanceRunStatus.QUARANTINED,
            }
        ),
    },
    GovernanceItemStage: {
        GovernanceItemStage.EXTRACTED: frozenset(
            {
                GovernanceItemStage.EVIDENCE_PENDING,
                GovernanceItemStage.DECIDING,
                GovernanceItemStage.FAILED,
            }
        ),
        GovernanceItemStage.EVIDENCE_PENDING: frozenset(
            {GovernanceItemStage.VERIFYING, GovernanceItemStage.FAILED}
        ),
        GovernanceItemStage.VERIFYING: frozenset(
            {GovernanceItemStage.DECIDING, GovernanceItemStage.FAILED}
        ),
        GovernanceItemStage.DECIDING: frozenset(
            {
                GovernanceItemStage.DECIDED,
                GovernanceItemStage.REVIEW_REQUIRED,
                GovernanceItemStage.FAILED,
            }
        ),
    },
    ReviewRequestStatus: {
        ReviewRequestStatus.PENDING: frozenset(
            {
                ReviewRequestStatus.APPROVED,
                ReviewRequestStatus.REJECTED,
                ReviewRequestStatus.CANCELLED,
            }
        )
    },
    PublicationRunStatus: {
        PublicationRunStatus.PLANNING: frozenset(
            {PublicationRunStatus.CURATING, PublicationRunStatus.FAILED}
        ),
        PublicationRunStatus.CURATING: frozenset(
            {PublicationRunStatus.VAULT_STAGED, PublicationRunStatus.FAILED}
        ),
        PublicationRunStatus.VAULT_STAGED: frozenset(
            {PublicationRunStatus.INDEXING, PublicationRunStatus.FAILED}
        ),
        PublicationRunStatus.INDEXING: frozenset(
            {PublicationRunStatus.INDEX_VERIFIED, PublicationRunStatus.FAILED}
        ),
        PublicationRunStatus.INDEX_VERIFIED: frozenset(
            {PublicationRunStatus.VAULT_PUBLISHED, PublicationRunStatus.FAILED}
        ),
        PublicationRunStatus.VAULT_PUBLISHED: frozenset(
            {PublicationRunStatus.ACTIVATING, PublicationRunStatus.FAILED}
        ),
        PublicationRunStatus.ACTIVATING: frozenset(
            {PublicationRunStatus.COMPLETED, PublicationRunStatus.FAILED}
        ),
        PublicationRunStatus.FAILED: frozenset(
            {
                PublicationRunStatus.CURATING,
                PublicationRunStatus.VAULT_STAGED,
                PublicationRunStatus.INDEXING,
                PublicationRunStatus.INDEX_VERIFIED,
                PublicationRunStatus.VAULT_PUBLISHED,
                PublicationRunStatus.ACTIVATING,
            }
        ),
    },
}


def can_transition(current: object, target: object) -> bool:
    """Return whether a state change is explicitly declared."""

    if not isinstance(current, StrEnum) or type(current) is not type(target):
        return False
    transitions = _TRANSITIONS.get(type(current), {})
    return target in transitions.get(current, frozenset())


def require_transition[StateT: StrEnum](current: StateT, target: StateT) -> StateT:
    """Return ``target`` or fail closed for an undeclared transition."""

    if not can_transition(current, target):
        raise InvalidStateTransitionError(
            "invalid state transition "
            f"({type(current).__name__}: {current.value} -> {target.value})"
        )
    return target


__all__ = ["can_transition", "require_transition"]
