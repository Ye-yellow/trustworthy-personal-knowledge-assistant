from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.domain import ClaimType, SourceVersionId, TrustTier
from trustworthy_kb.governance import (
    ClaimObject,
    ClaimScope,
    EvidenceMaterial,
    EvidencePackBuilder,
    EvidenceSearchHit,
    EvidenceSnapshotStore,
    FetchedEvidenceBlock,
    FetchedEvidenceDocument,
    PublicClaim,
    SearchIntent,
    store_search_manifest,
)
from trustworthy_kb.governance.errors import EvidencePackIntegrityError


def _claim() -> PublicClaim:
    return PublicClaim(
        claim_type=ClaimType.FACT,
        subject="Python",
        predicate="is",
        object=ClaimObject(value="a language", value_type="text"),
        scope=ClaimScope(domain="software"),
    )


def _material(host: str, rank: int, tier: TrustTier, intent: SearchIntent) -> EvidenceMaterial:
    text = f"Evidence from {host}"
    digest = hashlib.sha256(text.encode()).hexdigest()
    return EvidenceMaterial(
        search_hit=EvidenceSearchHit(
            candidate_id=f"search-{rank}",
            url=f"https://{host}/evidence",
            title=f"Evidence {rank}",
            provider_request_id="resp-synthetic",
            rank=rank,
            untrusted_snippet="provider text is not evidence",
        ),
        document=FetchedEvidenceDocument(
            normalized_url=f"https://{host}/evidence",
            final_url=f"https://{host}/evidence",
            raw_content_hash=digest,
            normalized_text_hash=digest,
            media_type="text/plain",
            byte_size=len(text),
            captured_at=datetime.now(UTC),
            freshness_metadata_hash="a" * 64,
            complete=True,
            extraction_status="COMPLETE",
            raw_snapshot_ref=f"raw:{digest}",
            extracted_snapshot_ref=f"extracted:{digest}",
            blocks=(FetchedEvidenceBlock(anchor="body", text=text, text_hash=digest),),
        ),
        source_version_id=SourceVersionId.generate(),
        trust_tier=tier,
        evidence_family=host,
        search_intent=intent,
    )


def test_evidence_pack_prioritizes_trust_and_independent_families(tmp_path: Path) -> None:
    store = EvidenceSnapshotStore(tmp_path)
    builder = EvidencePackBuilder(store, max_sources=2, max_blocks_per_source=1)
    materials = (
        _material("community.example", 0, TrustTier.T4, SearchIntent.SUPPORT),
        _material("official.example", 2, TrustTier.T1, SearchIntent.SUPPORT),
        _material("second.example", 1, TrustTier.T2, SearchIntent.CHALLENGE),
    )

    stored = builder.build(
        claim_fingerprint="b" * 64,
        claim=_claim(),
        search_policy_version="search-v1",
        query_hash="c" * 64,
        search_result_snapshot_hash="d" * 64,
        materials=materials,
    )

    assert [candidate.trust_tier for candidate in stored.pack.candidates] == [
        TrustTier.T1,
        TrustTier.T2,
    ]
    assert stored.pack.truncation_reason == "BUDGET_LIMIT"
    assert store.load_json(stored.snapshot_ref, stored.pack_hash) == stored.pack.model_dump(
        mode="json"
    )
    assert len(builder.verifier_candidates(stored, materials)) == 2


def test_evidence_pack_rejects_missing_or_tampered_excerpt(tmp_path: Path) -> None:
    store = EvidenceSnapshotStore(tmp_path)
    builder = EvidencePackBuilder(store)
    material = _material("official.example", 0, TrustTier.T1, SearchIntent.SUPPORT)
    stored = builder.build(
        claim_fingerprint="b" * 64,
        claim=_claim(),
        search_policy_version="search-v1",
        query_hash="c" * 64,
        search_result_snapshot_hash="d" * 64,
        materials=(material,),
    )
    with pytest.raises(EvidencePackIntegrityError, match="missing"):
        builder.verifier_candidates(stored, ())

    changed_block = material.document.blocks[0].model_copy(update={"text": "changed"})
    changed = material.model_copy(
        update={"document": material.document.model_copy(update={"blocks": (changed_block,)})}
    )
    with pytest.raises(EvidencePackIntegrityError, match="integrity"):
        builder.verifier_candidates(stored, (changed,))


def test_search_manifest_is_content_addressed_and_retains_untrusted_provenance(
    tmp_path: Path,
) -> None:
    store = EvidenceSnapshotStore(tmp_path)
    hit = _material("official.example", 0, TrustTier.T1, SearchIntent.SUPPORT).search_hit

    digest, reference = store_search_manifest(store, idempotency_hash="e" * 64, hits=(hit,))

    manifest = store.load_json(reference, digest)
    assert reference.endswith(".json")
    assert manifest["hits"][0]["untrusted_snippet"] == "provider text is not evidence"
