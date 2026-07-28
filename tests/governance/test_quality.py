from __future__ import annotations

import pytest

from trustworthy_kb.domain import (
    ClaimStatus,
    ClaimType,
    EvidenceStance,
    QualityVerdict,
    Sensitivity,
    SourceVersionId,
    TrustTier,
)
from trustworthy_kb.governance import (
    ClaimDraft,
    ClaimObject,
    ClaimOriginSpan,
    ClaimScope,
    EvidencePack,
    EvidencePackCandidate,
    EvidenceVerificationOutput,
    PublicClaim,
    QualityPolicyEngine,
    SearchIntent,
)
from trustworthy_kb.governance.contracts import CandidateVerification
from trustworthy_kb.governance.errors import EvidencePackIntegrityError


def _claim(
    claim_type: ClaimType = ClaimType.FACT,
    *,
    domain: str = "software",
    owner: str | None = None,
    version: str | None = None,
) -> ClaimDraft:
    return ClaimDraft(
        claim_type=claim_type,
        subject="Python",
        predicate="is",
        object=ClaimObject(value="a language", value_type="text"),
        scope=ClaimScope(domain=domain, owner=owner, version=version),
        sensitivity=Sensitivity.PUBLIC,
        origins=(ClaimOriginSpan(block_anchor="p:1", start=0, end=6),),
    )


def _pack(*, contradictory: bool = False) -> tuple[EvidencePack, EvidenceVerificationOutput]:
    candidates = [
        EvidencePackCandidate(
            candidate_id="support",
            source_version_id=SourceVersionId.generate(),
            anchor="body",
            excerpt_hash="a" * 64,
            trust_tier=TrustTier.T1,
            complete=True,
            evidence_family="official.example",
            search_intent=SearchIntent.SUPPORT,
        )
    ]
    results = [
        CandidateVerification(
            candidate_id="support",
            stance=EvidenceStance.SUPPORTS,
            supported_claim_fields=("subject", "predicate", "object"),
            evidence_coverage=1,
            scope_match=True,
            version_match=True,
            freshness_match=True,
            relevance=1,
            reason_codes=("DIRECT_SUPPORT",),
        )
    ]
    if contradictory:
        candidates.append(
            candidates[0].model_copy(
                update={
                    "candidate_id": "challenge",
                    "source_version_id": SourceVersionId.generate(),
                    "evidence_family": "second.example",
                    "search_intent": SearchIntent.CHALLENGE,
                }
            )
        )
        results.append(
            results[0].model_copy(
                update={"candidate_id": "challenge", "stance": EvidenceStance.CONTRADICTS}
            )
        )
    pack = EvidencePack(
        claim_fingerprint="b" * 64,
        claim=PublicClaim(
            claim_type=ClaimType.FACT,
            subject="Python",
            predicate="is",
            object=ClaimObject(value="a language", value_type="text"),
            scope=ClaimScope(domain="software"),
        ),
        search_policy_version="search-v1",
        query_hash="c" * 64,
        search_result_snapshot_hash="d" * 64,
        ordered_candidate_ids=tuple(item.candidate_id for item in candidates),
        candidates=tuple(candidates),
        max_candidates=8,
        max_evidence_blocks=16,
    )
    return pack, EvidenceVerificationOutput(results=tuple(results))


def test_policy_handles_personal_subjective_missing_and_safety_paths() -> None:
    engine = QualityPolicyEngine()
    empty = EvidenceVerificationOutput(results=())

    asserted = engine.evaluate(
        claim=_claim(ClaimType.PREFERENCE, owner="me"),
        origin_trust_tier=TrustTier.T0,
        pack=None,
        verification=empty,
        search_available=True,
    )
    opinion = engine.evaluate(
        claim=_claim(ClaimType.OPINION),
        origin_trust_tier=TrustTier.T0,
        pack=None,
        verification=empty,
        search_available=True,
    )
    missing = engine.evaluate(
        claim=_claim(),
        origin_trust_tier=TrustTier.T0,
        pack=None,
        verification=empty,
        search_available=False,
    )
    quarantined = engine.evaluate(
        claim=_claim(),
        origin_trust_tier=TrustTier.T0,
        pack=None,
        verification=empty,
        search_available=True,
        safety_signals=("PROMPT_INJECTION",),
    )

    assert asserted.verdict is QualityVerdict.USER_ASSERTED
    assert opinion.verdict is QualityVerdict.OPINION
    assert missing.verdict is QualityVerdict.INSUFFICIENT and missing.review_required
    assert quarantined.claim_status is ClaimStatus.QUARANTINED


def test_policy_auto_verifies_only_low_risk_and_routes_high_risk_to_review() -> None:
    pack, verification = _pack()
    engine = QualityPolicyEngine()

    low = engine.evaluate(
        claim=_claim(),
        origin_trust_tier=TrustTier.T0,
        pack=pack,
        verification=verification,
        search_available=True,
    )
    high = engine.evaluate(
        claim=_claim(domain="medical"),
        origin_trust_tier=TrustTier.T0,
        pack=pack,
        verification=verification,
        search_available=True,
    )

    assert low.verdict is QualityVerdict.VERIFIED and low.publishable
    assert high.verdict is QualityVerdict.VERIFIED
    assert high.review_required and not high.publishable


def test_policy_marks_authoritative_conflict_contested_and_rejects_pack_mismatch() -> None:
    pack, verification = _pack(contradictory=True)
    decision = QualityPolicyEngine().evaluate(
        claim=_claim(),
        origin_trust_tier=TrustTier.T0,
        pack=pack,
        verification=verification,
        search_available=True,
    )
    assert decision.verdict is QualityVerdict.CONTESTED
    assert decision.review_required

    with pytest.raises(EvidencePackIntegrityError, match="frozen evidence pack"):
        QualityPolicyEngine().evaluate(
            claim=_claim(),
            origin_trust_tier=TrustTier.T0,
            pack=pack,
            verification=EvidenceVerificationOutput(results=verification.results[:1]),
            search_available=True,
        )
