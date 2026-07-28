"""Provider-neutral evidence-search boundary and privacy-safe query builder."""

from __future__ import annotations

import json
from typing import Protocol

from trustworthy_kb.governance.contracts import (
    EvidenceSearchHit,
    EvidenceSearchRequest,
    PublicClaim,
    SearchCapabilities,
)


class EvidenceSearchGateway(Protocol):
    """Discover candidate public URLs without treating model text as evidence."""

    def capabilities(self) -> SearchCapabilities:
        """Return stable transport and tool capabilities."""

    async def search(self, request: EvidenceSearchRequest) -> tuple[EvidenceSearchHit, ...]:
        """Return ranked, untrusted candidate URLs."""


def build_public_search_prompt(request: EvidenceSearchRequest) -> str:
    """Build a bounded query containing only the request's public structured fields."""

    claim = _public_claim_payload(request.claim)
    constraints = {
        "time": list(request.time_constraints),
        "version": list(request.version_constraints),
        "scope": list(request.scope_constraints),
    }
    payload = json.dumps(
        {"claim": claim, "intent": request.intent.value, "constraints": constraints},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "Find authoritative public web sources for the structured claim below. "
        "Search for sources only; do not use private context or invent URLs. "
        f"Return a short source-oriented answer. INPUT={payload}"
    )


def _public_claim_payload(claim: PublicClaim) -> dict[str, object]:
    return {
        "claim_type": claim.claim_type.value,
        "subject": claim.subject,
        "predicate": claim.predicate,
        "object": claim.object.model_dump(mode="json", exclude_none=True),
        "scope": claim.scope.model_dump(mode="json", exclude_none=True),
    }


__all__ = ["EvidenceSearchGateway", "build_public_search_prompt"]
