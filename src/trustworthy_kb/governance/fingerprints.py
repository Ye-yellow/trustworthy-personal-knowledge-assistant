"""Deterministic claim identity and request hashing."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from trustworthy_kb.governance.contracts import ClaimDraft

_TRANSIENT_SCOPE_FIELDS = frozenset({"lifecycle_status"})


def canonical_json_hash(value: Any) -> str:
    """Hash a value after stable JSON serialization."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def claim_fingerprint(claim: ClaimDraft) -> str:
    """Return the exact semantic identity for a claim draft."""

    return canonical_json_hash(_claim_identity(claim, family=False))


def claim_family_key(claim: ClaimDraft) -> str:
    """Return the stable fact-family identity, excluding value and transient scope."""

    return canonical_json_hash(_claim_identity(claim, family=True))


def _claim_identity(claim: ClaimDraft, *, family: bool) -> dict[str, Any]:
    scope = claim.scope.model_dump(mode="json", exclude_none=True)
    if family:
        scope = {key: value for key, value in scope.items() if key not in _TRANSIENT_SCOPE_FIELDS}
    result: dict[str, Any] = {
        "claim_type": claim.claim_type.value,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "scope": scope,
    }
    if not family:
        result.update(
            {
                "object": claim.object.model_dump(mode="json", exclude_none=True),
                "valid_from": claim.valid_from,
                "valid_to": claim.valid_to,
                "freshness_at": claim.freshness_at,
                "sensitivity": claim.sensitivity.value,
            }
        )
    return result


def _json_default(value: object) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


__all__ = ["canonical_json_hash", "claim_family_key", "claim_fingerprint"]
