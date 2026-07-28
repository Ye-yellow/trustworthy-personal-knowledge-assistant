from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trustworthy_kb.domain import ActorType, EntityType, SourceId
from trustworthy_kb.domain.audit_hash import canonical_audit_json, operation_log_entry_hash
from trustworthy_kb.domain.errors import InvariantViolationError


def test_canonical_audit_json_is_order_independent_and_compact() -> None:
    assert canonical_audit_json({"b": 2, "a": {"d": 4, "c": 3}}) == canonical_audit_json(
        {"a": {"c": 3, "d": 4}, "b": 2}
    )
    assert canonical_audit_json({"value": "知识"}).decode() == '{"value":"知识"}'


def test_operation_hash_is_deterministic_and_sensitive_to_chain() -> None:
    source_id = SourceId("source_01ARZ3NDEKTSV4RRFFQ69G5FAV")
    arguments = {
        "operation_id": "op-1",
        "step_number": 0,
        "actor_type": ActorType.SYSTEM,
        "actor_id": None,
        "action": "CREATE",
        "target_type": EntityType.SOURCE,
        "target_id": source_id,
        "before_json": {},
        "after_json": {"revision": 1},
        "created_at": datetime(2026, 7, 28, tzinfo=UTC),
    }
    first = operation_log_entry_hash(previous_entry_hash=None, **arguments)
    assert first == operation_log_entry_hash(previous_entry_hash=None, **arguments)
    assert first != operation_log_entry_hash(previous_entry_hash="a" * 64, **arguments)
    assert len(first) == 64


def test_canonical_audit_json_rejects_non_json_and_non_finite_values() -> None:
    with pytest.raises(InvariantViolationError, match="audit payload is not canonical JSON"):
        canonical_audit_json({"invalid": object()})
    with pytest.raises(InvariantViolationError, match="audit payload is not canonical JSON"):
        canonical_audit_json({"invalid": float("nan")})
