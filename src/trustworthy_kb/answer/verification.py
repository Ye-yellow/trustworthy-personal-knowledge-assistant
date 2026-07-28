"""Closed-set deterministic validation for generated answer citations."""

from __future__ import annotations

from collections.abc import Sequence

from trustworthy_kb.answer.contracts import (
    AnswerDraft,
    AnswerEvidence,
    CitationVerificationOutput,
)
from trustworthy_kb.answer.errors import AnswerIntegrityError


def validate_citation_closed_set(
    draft: AnswerDraft,
    evidence: Sequence[AnswerEvidence],
    *,
    max_claims: int = 12,
    max_claim_characters: int = 1000,
) -> None:
    """Reject invented citations, duplicates, and output outside configured budgets."""

    if max_claims < 1 or max_claim_characters < 1:
        raise ValueError("answer validation limits must be positive")
    if len(draft.claims) > max_claims:
        raise AnswerIntegrityError("answer draft exceeds the configured claim limit")
    allowed = {item.chunk_id for item in evidence}
    if len(allowed) != len(evidence):
        raise AnswerIntegrityError("answer evidence chunk IDs are not unique")
    if not allowed:
        raise AnswerIntegrityError("answer draft cannot be validated without evidence")
    for claim in draft.claims:
        if len(claim.statement) > max_claim_characters:
            raise AnswerIntegrityError("answer claim exceeds the configured character limit")
        if not set(claim.citation_chunk_ids).issubset(allowed):
            raise AnswerIntegrityError("answer claim cites evidence outside the retrieved set")


def validate_semantic_support(
    draft: AnswerDraft,
    verification: CitationVerificationOutput,
) -> None:
    """Require exactly one supported verifier decision for every generated claim."""

    by_index = {item.claim_index: item for item in verification.decisions}
    expected = set(range(len(draft.claims)))
    if len(by_index) != len(verification.decisions) or set(by_index) != expected:
        raise AnswerIntegrityError(
            "citation verifier did not decide every answer claim exactly once"
        )
    for index, claim in enumerate(draft.claims):
        decision = by_index[index]
        cited = set(claim.citation_chunk_ids)
        supporting = set(decision.supporting_chunk_ids)
        if not decision.supported or not supporting or not supporting.issubset(cited):
            raise AnswerIntegrityError("an answer claim is not supported by its cited evidence")


__all__ = ["validate_citation_closed_set", "validate_semantic_support"]
