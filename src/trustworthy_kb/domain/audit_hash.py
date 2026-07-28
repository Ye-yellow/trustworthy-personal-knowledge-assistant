"""Canonical, content-free hashing for append-only operation logs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from trustworthy_kb.domain.base import DomainJson
from trustworthy_kb.domain.enums import ActorType, EntityType
from trustworthy_kb.domain.errors import InvariantViolationError
from trustworthy_kb.domain.ids import TypedId


def canonical_audit_json(value: DomainJson) -> bytes:
    """Encode a JSON object deterministically or fail without echoing its content."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise InvariantViolationError("audit payload is not canonical JSON") from error
    return encoded.encode("utf-8")


def operation_log_entry_hash(
    *,
    operation_id: str,
    step_number: int,
    actor_type: ActorType,
    actor_id: str | None,
    action: str,
    target_type: EntityType,
    target_id: TypedId,
    before_json: DomainJson,
    after_json: DomainJson,
    previous_entry_hash: str | None,
    created_at: datetime,
) -> str:
    """Compute the SHA-256 link for one operation-log entry."""

    payload: DomainJson = {
        "action": action,
        "actor_id": actor_id,
        "actor_type": actor_type.value,
        "after": after_json,
        "before": before_json,
        "created_at": created_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "operation_id": operation_id,
        "previous_entry_hash": previous_entry_hash,
        "step_number": step_number,
        "target_id": str(target_id),
        "target_type": target_type.value,
    }
    return hashlib.sha256(canonical_audit_json(payload)).hexdigest()


__all__ = ["canonical_audit_json", "operation_log_entry_hash"]
