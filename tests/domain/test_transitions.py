from __future__ import annotations

import pytest

from trustworthy_kb.domain import (
    ClaimStatus,
    CuratedVersionStatus,
    IdempotencyStatus,
    IndexGenerationStatus,
    IndexJobStatus,
    InvalidStateTransitionError,
    KnowledgeChangeStatus,
    ModelRunStatus,
    SourceVersionStatus,
    can_transition,
    require_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SourceVersionStatus.CAPTURED, SourceVersionStatus.PARSED),
        (SourceVersionStatus.PARSE_FAILED, SourceVersionStatus.PARSED),
        (ClaimStatus.PROPOSED, ClaimStatus.VERIFIED),
        (ClaimStatus.VERIFIED, ClaimStatus.SUPERSEDED),
        (CuratedVersionStatus.DRAFT, CuratedVersionStatus.VALIDATING),
        (IndexGenerationStatus.STAGING, IndexGenerationStatus.ACTIVE),
        (IndexJobStatus.FAILED, IndexJobStatus.PENDING),
        (KnowledgeChangeStatus.PUBLISH_INTENT, KnowledgeChangeStatus.ACTIVE),
        (ModelRunStatus.STARTED, ModelRunStatus.SUCCEEDED),
        (IdempotencyStatus.UNKNOWN, IdempotencyStatus.FAILED),
    ],
)
def test_declared_transitions_are_allowed(current: object, target: object) -> None:
    assert can_transition(current, target)
    assert require_transition(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SourceVersionStatus.READY, SourceVersionStatus.PARSED),
        (ClaimStatus.CONTESTED, ClaimStatus.VERIFIED),
        (CuratedVersionStatus.ACTIVE, CuratedVersionStatus.DRAFT),
        (IndexGenerationStatus.FAILED, IndexGenerationStatus.ACTIVE),
        (IndexJobStatus.DELETED, IndexJobStatus.PENDING),
        (KnowledgeChangeStatus.FAILED, KnowledgeChangeStatus.VALIDATING),
        (ModelRunStatus.SUCCEEDED, ModelRunStatus.STARTED),
        (IdempotencyStatus.SUCCEEDED, IdempotencyStatus.IN_PROGRESS),
    ],
)
def test_undeclared_transitions_fail_closed(current: object, target: object) -> None:
    assert not can_transition(current, target)
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        require_transition(current, target)

    assert current.value in str(exc_info.value)  # type: ignore[union-attr]
    assert target.value in str(exc_info.value)  # type: ignore[union-attr]


def test_transition_between_different_state_machines_is_rejected() -> None:
    assert not can_transition(ClaimStatus.PROPOSED, ModelRunStatus.SUCCEEDED)
    with pytest.raises(InvalidStateTransitionError):
        require_transition(ClaimStatus.PROPOSED, ModelRunStatus.SUCCEEDED)
