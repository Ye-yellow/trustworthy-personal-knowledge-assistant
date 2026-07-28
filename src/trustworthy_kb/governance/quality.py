"""Deterministic, model-independent claim quality policy."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from trustworthy_kb.domain import (
    ClaimStatus,
    ClaimType,
    EvidenceStance,
    QualityVerdict,
    RiskLevel,
    TrustTier,
)
from trustworthy_kb.governance.contracts import (
    ClaimDraft,
    EvidencePack,
    EvidenceVerificationOutput,
    QualityDimensions,
    QualityMetric,
)
from trustworthy_kb.governance.errors import EvidencePackIntegrityError

_HIGH_RISK_DOMAINS = frozenset(
    {"medical", "medicine", "health", "legal", "law", "financial", "finance", "security"}
)
_SUBJECTIVE_TYPES = frozenset({ClaimType.OPINION, ClaimType.PREDICTION})
_USER_ASSERTED_TYPES = frozenset(
    {ClaimType.USER_EXPERIENCE, ClaimType.PREFERENCE, ClaimType.DECISION}
)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: QualityVerdict
    claim_status: ClaimStatus
    risk_level: RiskLevel
    reason_code: str
    reason_summary: str
    review_required: bool
    publishable: bool
    dimensions: QualityDimensions


class QualityPolicyEngine:
    """Apply frozen thresholds; model outputs can never select the verdict."""

    def evaluate(
        self,
        *,
        claim: ClaimDraft,
        origin_trust_tier: TrustTier,
        pack: EvidencePack | None,
        verification: EvidenceVerificationOutput,
        search_available: bool,
        safety_signals: tuple[str, ...] = (),
    ) -> PolicyDecision:
        risk = _risk_level(claim)
        dimensions = _dimensions(risk, pack, verification, safety_signals)
        if safety_signals:
            return _decision(
                QualityVerdict.QUARANTINED,
                ClaimStatus.QUARANTINED,
                RiskLevel.HIGH,
                "SAFETY_BLOCKED",
                "Claim or evidence failed deterministic safety checks.",
                dimensions,
                review=False,
                publishable=False,
            )
        if claim.claim_type in _SUBJECTIVE_TYPES:
            return _decision(
                QualityVerdict.OPINION,
                ClaimStatus.OPINION,
                risk,
                "SUBJECTIVE_CLAIM",
                "Subjective or predictive content is retained as opinion, not public fact.",
                dimensions,
                review=risk is not RiskLevel.LOW,
                publishable=risk is RiskLevel.LOW,
            )
        if (
            origin_trust_tier is TrustTier.T0
            and claim.claim_type in _USER_ASSERTED_TYPES
            and claim.scope.owner
        ):
            return _decision(
                QualityVerdict.USER_ASSERTED,
                ClaimStatus.USER_ASSERTED,
                risk,
                "OWNER_SCOPED_ASSERTION",
                "Owner-scoped personal content is retained as a user assertion.",
                dimensions,
                review=risk is not RiskLevel.LOW,
                publishable=risk is RiskLevel.LOW,
            )
        if not search_available:
            return _insufficient(risk, dimensions, "SEARCH_CAPABILITY_UNAVAILABLE")
        if pack is None or not verification.results:
            return _insufficient(risk, dimensions, "EVIDENCE_MISSING")

        candidate_by_id = {candidate.candidate_id: candidate for candidate in pack.candidates}
        verification_ids = {item.candidate_id for item in verification.results}
        if verification_ids != set(candidate_by_id):
            raise EvidencePackIntegrityError(
                "quality policy input does not match the frozen evidence pack"
            )
        trusted_support = [
            item
            for item in verification.results
            if item.stance is EvidenceStance.SUPPORTS
            and candidate_by_id[item.candidate_id].trust_tier in {TrustTier.T1, TrustTier.T2}
            and item.relevance >= 0.8
        ]
        trusted_contradiction = [
            item
            for item in verification.results
            if item.stance is EvidenceStance.CONTRADICTS
            and candidate_by_id[item.candidate_id].trust_tier in {TrustTier.T1, TrustTier.T2}
            and item.relevance >= 0.8
        ]
        if trusted_support and trusted_contradiction:
            return _decision(
                QualityVerdict.CONTESTED,
                ClaimStatus.CONTESTED,
                risk,
                "TRUSTED_EVIDENCE_CONFLICT",
                "Authoritative supporting and contradicting evidence overlap.",
                dimensions,
                review=True,
                publishable=False,
            )
        coverage = max((item.evidence_coverage for item in trusted_support), default=0.0)
        version_required = claim.claim_type is ClaimType.CODE_BEHAVIOR or bool(claim.scope.version)
        freshness_required = claim.freshness_at is not None
        version_ok = not version_required or any(
            item.version_match is True for item in trusted_support
        )
        freshness_ok = not freshness_required or any(
            item.freshness_match is True for item in trusted_support
        )
        if trusted_support and coverage >= 0.95 and version_ok and freshness_ok:
            return _decision(
                QualityVerdict.VERIFIED,
                ClaimStatus.VERIFIED,
                risk,
                "AUTHORITATIVE_SUPPORT",
                "Authoritative evidence meets coverage, scope, version, and freshness policy.",
                dimensions,
                review=risk is not RiskLevel.LOW,
                publishable=risk is RiskLevel.LOW,
            )
        contradiction_coverage = max(
            (item.evidence_coverage for item in trusted_contradiction), default=0.0
        )
        if trusted_contradiction and not trusted_support and contradiction_coverage >= 0.95:
            return _decision(
                QualityVerdict.REJECTED,
                ClaimStatus.REJECTED,
                risk,
                "AUTHORITATIVE_CONTRADICTION",
                "Authoritative evidence fully contradicts the claim.",
                dimensions,
                review=risk is not RiskLevel.LOW,
                publishable=False,
            )
        return _insufficient(risk, dimensions, "EVIDENCE_COVERAGE_INSUFFICIENT")


def _dimensions(
    risk: RiskLevel,
    pack: EvidencePack | None,
    verification: EvidenceVerificationOutput,
    safety_signals: tuple[str, ...],
) -> QualityDimensions:
    results = verification.results
    candidates = () if pack is None else pack.candidates
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    coverage = max((result.evidence_coverage for result in results), default=None)
    trusted = [
        result
        for result in results
        if by_id.get(result.candidate_id) is not None
        and by_id[result.candidate_id].trust_tier in {TrustTier.T1, TrustTier.T2}
    ]
    families = {
        by_id[result.candidate_id].evidence_family
        for result in results
        if result.candidate_id in by_id
    }
    stances = {result.stance for result in trusted}
    return QualityDimensions(
        evidence_coverage=_metric(coverage, pass_at=0.95),
        source_reliability=_metric(1.0 if trusted else (0.4 if results else None), pass_at=0.8),
        source_independence=_metric(min(len(families) / 2, 1.0) if results else None, pass_at=1.0),
        source_agreement=_metric(
            None
            if not trusted
            else 0.0
            if {EvidenceStance.SUPPORTS, EvidenceStance.CONTRADICTS}.issubset(stances)
            else 1.0,
            pass_at=1.0,
        ),
        freshness=_boolean_metric([result.freshness_match for result in results]),
        version_match=_boolean_metric([result.version_match for result in results]),
        extraction_quality=_metric(
            (sum(candidate.complete for candidate in candidates) / len(candidates))
            if candidates
            else None,
            pass_at=1.0,
        ),
        verifier_agreement=_metric(
            1.0 if results and len(stances) <= 1 else 0.0 if results else None, pass_at=1.0
        ),
        risk_level=risk,
        safety_status="UNSAFE" if safety_signals else "SAFE",
    )


def _risk_level(claim: ClaimDraft) -> RiskLevel:
    domain = (claim.scope.domain or "").strip().lower()
    if domain in _HIGH_RISK_DOMAINS:
        return RiskLevel.HIGH
    if claim.claim_type in _SUBJECTIVE_TYPES | _USER_ASSERTED_TYPES:
        return RiskLevel.LOW
    return RiskLevel.LOW if claim.sensitivity.value == "public" else RiskLevel.MEDIUM


def _metric(value: float | None, *, pass_at: float) -> QualityMetric:
    if value is None:
        return QualityMetric(state="UNKNOWN")
    return QualityMetric(state="PASS" if value >= pass_at else "FAIL", score=value)


def _boolean_metric(values: list[bool | None]) -> QualityMetric:
    known = [value for value in values if value is not None]
    if not known:
        return QualityMetric(state="NOT_APPLICABLE")
    score = sum(known) / len(known)
    return QualityMetric(state="PASS" if score == 1 else "FAIL", score=score)


def _insufficient(
    risk: RiskLevel, dimensions: QualityDimensions, reason_code: str
) -> PolicyDecision:
    return _decision(
        QualityVerdict.INSUFFICIENT,
        ClaimStatus.INSUFFICIENT,
        risk,
        reason_code,
        "Independent evidence is unavailable or does not meet the frozen policy.",
        dimensions,
        review=True,
        publishable=False,
    )


def _decision(
    verdict: QualityVerdict,
    claim_status: ClaimStatus,
    risk: RiskLevel,
    reason_code: str,
    reason_summary: str,
    dimensions: QualityDimensions,
    *,
    review: bool,
    publishable: bool,
) -> PolicyDecision:
    return PolicyDecision(
        verdict=verdict,
        claim_status=claim_status,
        risk_level=risk,
        reason_code=reason_code,
        reason_summary=reason_summary,
        review_required=review,
        publishable=publishable,
        dimensions=dimensions.model_copy(update={"risk_level": risk}),
    )


__all__ = ["PolicyDecision", "QualityPolicyEngine"]
