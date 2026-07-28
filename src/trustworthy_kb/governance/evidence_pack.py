"""Deterministic evidence budgeting and content-addressed pack creation."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from trustworthy_kb.domain import SourceVersionId, TrustTier
from trustworthy_kb.governance.contracts import (
    EvidencePack,
    EvidencePackCandidate,
    EvidenceSearchHit,
    FetchedEvidenceDocument,
    PublicClaim,
    SearchIntent,
)
from trustworthy_kb.governance.errors import EvidencePackIntegrityError
from trustworthy_kb.governance.snapshot_store import EvidenceSnapshotStore
from trustworthy_kb.governance.verifier import VerifierCandidate

_TRUST_ORDER = {
    TrustTier.T1: 0,
    TrustTier.T2: 1,
    TrustTier.T0: 2,
    TrustTier.T3: 3,
    TrustTier.T4: 4,
    TrustTier.T5: 5,
}


class EvidenceMaterial(BaseModel):
    """Fetched source material after it has a persisted source version identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    search_hit: EvidenceSearchHit
    document: FetchedEvidenceDocument
    source_version_id: SourceVersionId
    trust_tier: TrustTier
    evidence_family: str = Field(min_length=1, max_length=253)
    search_intent: SearchIntent
    version: str | None = None


class StoredEvidencePack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pack: EvidencePack
    pack_hash: str
    snapshot_ref: str


class EvidencePackBuilder:
    """Prioritize authoritative independent sources under explicit budgets."""

    def __init__(
        self,
        store: EvidenceSnapshotStore,
        *,
        max_sources: int = 8,
        max_blocks_per_source: int = 2,
        max_evidence_blocks: int = 16,
    ) -> None:
        self._store = store
        self._max_sources = max_sources
        self._max_blocks_per_source = max_blocks_per_source
        self._max_evidence_blocks = max_evidence_blocks

    def build(
        self,
        *,
        claim_fingerprint: str,
        claim: PublicClaim,
        search_policy_version: str,
        query_hash: str,
        search_result_snapshot_hash: str,
        materials: tuple[EvidenceMaterial, ...],
    ) -> StoredEvidencePack:
        ordered = _prioritize_materials(materials)[: self._max_sources]
        candidates: list[EvidencePackCandidate] = []
        for material in ordered:
            for position, block in enumerate(
                material.document.blocks[: self._max_blocks_per_source]
            ):
                if len(candidates) >= self._max_evidence_blocks:
                    break
                candidate_id = _candidate_id(material.search_hit.candidate_id, position)
                candidates.append(
                    EvidencePackCandidate(
                        candidate_id=candidate_id,
                        source_version_id=material.source_version_id,
                        anchor=block.anchor,
                        excerpt_hash=block.text_hash,
                        trust_tier=material.trust_tier,
                        version=material.version,
                        complete=material.document.complete,
                        evidence_family=material.evidence_family,
                        search_intent=material.search_intent,
                    )
                )
        selected_sources = {candidate.source_version_id for candidate in candidates}
        truncated = len(materials) > len(selected_sources) or any(
            len(material.document.blocks) > self._max_blocks_per_source for material in ordered
        )
        pack = EvidencePack(
            claim_fingerprint=claim_fingerprint,
            claim=claim,
            search_policy_version=search_policy_version,
            query_hash=query_hash,
            search_result_snapshot_hash=search_result_snapshot_hash,
            ordered_candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
            candidates=tuple(candidates),
            max_candidates=self._max_sources,
            max_evidence_blocks=self._max_evidence_blocks,
            truncation_reason="BUDGET_LIMIT" if truncated else None,
        )
        pack_hash, reference = self._store.put_json("packs", pack.model_dump(mode="json"))
        return StoredEvidencePack(pack=pack, pack_hash=pack_hash, snapshot_ref=reference)

    def verifier_candidates(
        self,
        stored: StoredEvidencePack,
        materials: tuple[EvidenceMaterial, ...],
    ) -> tuple[VerifierCandidate, ...]:
        by_location = {
            (material.source_version_id, block.anchor): (material, block.text)
            for material in materials
            for block in material.document.blocks
        }
        result: list[VerifierCandidate] = []
        for candidate in stored.pack.candidates:
            material_and_text = by_location.get((candidate.source_version_id, candidate.anchor))
            if material_and_text is None:
                raise EvidencePackIntegrityError("evidence pack references missing snapshot text")
            material, excerpt = material_and_text
            if hashlib.sha256(excerpt.encode()).hexdigest() != candidate.excerpt_hash:
                raise EvidencePackIntegrityError("evidence excerpt integrity check failed")
            result.append(
                VerifierCandidate(
                    candidate_id=candidate.candidate_id,
                    anchor=candidate.anchor,
                    excerpt=excerpt,
                    trust_tier=candidate.trust_tier,
                    version=material.version,
                    complete=candidate.complete,
                )
            )
        return tuple(result)


def store_search_manifest(
    store: EvidenceSnapshotStore,
    *,
    idempotency_hash: str,
    hits: tuple[EvidenceSearchHit, ...],
) -> tuple[str, str]:
    """Persist provider candidates, including untrusted snippets, outside SQLite."""

    return store.put_json(
        "search",
        {
            "idempotency_hash": idempotency_hash,
            "hits": [hit.model_dump(mode="json") for hit in hits],
        },
    )


def _prioritize_materials(
    materials: tuple[EvidenceMaterial, ...],
) -> tuple[EvidenceMaterial, ...]:
    ordered = sorted(
        materials,
        key=lambda material: (
            _TRUST_ORDER[material.trust_tier],
            material.search_hit.rank,
            material.evidence_family,
            str(material.document.final_url),
        ),
    )
    diverse: list[EvidenceMaterial] = []
    repeated: list[EvidenceMaterial] = []
    families: set[str] = set()
    for material in ordered:
        if material.evidence_family in families:
            repeated.append(material)
        else:
            families.add(material.evidence_family)
            diverse.append(material)
    return tuple((*diverse, *repeated))


def _candidate_id(search_candidate_id: str, position: int) -> str:
    digest = hashlib.sha256(f"{search_candidate_id}:{position}".encode()).hexdigest()[:24]
    return f"evidence_candidate_{digest}"


__all__ = [
    "EvidenceMaterial",
    "EvidencePackBuilder",
    "StoredEvidencePack",
    "store_search_manifest",
]
