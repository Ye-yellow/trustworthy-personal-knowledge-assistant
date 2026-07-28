from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trustworthy_kb.domain import (
    ClaimId,
    ClaimRecord,
    ClaimStatus,
    ClaimType,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeChangeId,
    KnowledgeNoteId,
    QualityCheckId,
    Sensitivity,
    SourceId,
    SourceVersionId,
)
from trustworthy_kb.publication.chunking import MarkdownChunker
from trustworthy_kb.publication.contracts import CurationGroup, CurationPlan
from trustworthy_kb.publication.curation import (
    CuratedMarkdownRenderer,
    DeterministicCurationPlanner,
    curation_claims,
    verify_curated_markdown,
)
from trustworthy_kb.publication.errors import ChunkingError, CurationError


def _claim(
    *,
    status: ClaimStatus = ClaimStatus.VERIFIED,
    sensitivity: Sensitivity = Sensitivity.PRIVATE,
    subject: str = "Milvus",
    value: str = "supports hybrid retrieval",
) -> ClaimRecord:
    now = datetime.now(UTC)
    return ClaimRecord(
        id=ClaimId.generate(),
        claim_fingerprint="1" * 64,
        claim_family_key="2" * 64,
        claim_type=ClaimType.FACT,
        subject=subject,
        predicate="capability",
        object_json={"value": value},
        scope_json={},
        sensitivity=sensitivity,
        status=status,
        current_quality_check_id=QualityCheckId.generate(),
        revision=2,
        created_at=now,
        updated_at=now,
    )


async def _artifact(*records: ClaimRecord):
    claims = curation_claims(records)
    plan = await DeterministicCurationPlanner().plan(claims)
    artifact = CuratedMarkdownRenderer().render(
        note_id=KnowledgeNoteId.generate(),
        curated_version_id=CuratedVersionId.generate(),
        based_on_change_id=KnowledgeChangeId.generate(),
        version_number=1,
        plan=plan,
        claims=claims,
        source_ids=(SourceId.generate(),),
        source_version_ids=(SourceVersionId.generate(),),
        model_name="sub2api/gpt-5.5",
        prompt_version="curation-v1",
        quality_policy_version="l3-v1",
        created_at=datetime.now(UTC),
    )
    return artifact, claims


async def test_curation_filters_unpublishable_claims_and_preserves_exact_ids() -> None:
    verified = _claim()
    personal = _claim(status=ClaimStatus.USER_ASSERTED, subject="User")
    rejected = _claim(status=ClaimStatus.REJECTED)

    artifact, claims = await _artifact(verified, personal, rejected)

    assert artifact.claim_ids == (verified.id, personal.id)
    assert artifact.sensitivity is Sensitivity.PRIVATE
    assert set(artifact.quality_statuses) == {
        ClaimStatus.VERIFIED,
        ClaimStatus.USER_ASSERTED,
    }
    assert all(str(claim.id) in artifact.body_markdown for claim in claims)
    metadata = verify_curated_markdown(artifact.markdown, expected_hash=artifact.content_hash)
    assert metadata["curated_version_id"] == str(artifact.curated_version_id)
    assert metadata["status"] == "active"


async def test_curation_rejects_missing_quality_and_model_plan_drift() -> None:
    record = _claim().model_copy(update={"current_quality_check_id": None})
    with pytest.raises(CurationError, match="quality"):
        curation_claims((record,))

    publishable = curation_claims((_claim(), _claim(subject="SQLite")))
    invalid = CurationPlan(
        title="Invalid",
        groups=(CurationGroup(heading="One", claim_ids=(publishable[0].id,)),),
    )
    with pytest.raises(CurationError, match="exactly once"):
        CuratedMarkdownRenderer().render(
            note_id=KnowledgeNoteId.generate(),
            curated_version_id=CuratedVersionId.generate(),
            based_on_change_id=KnowledgeChangeId.generate(),
            version_number=1,
            plan=invalid,
            claims=publishable,
            source_ids=(SourceId.generate(),),
            source_version_ids=(SourceVersionId.generate(),),
            model_name="gpt-5.5",
            prompt_version="v1",
            quality_policy_version="v1",
            created_at=datetime.now(UTC),
        )


async def test_curated_hash_detects_body_and_frontmatter_tampering() -> None:
    artifact, _ = await _artifact(_claim(value="safe value"))

    with pytest.raises(CurationError, match="hash"):
        verify_curated_markdown(artifact.markdown.replace("safe value", "changed value"))
    with pytest.raises(CurationError, match="hash"):
        verify_curated_markdown(artifact.markdown.replace("status: active", "status: draft"))


async def test_chunker_is_stable_complete_and_generation_bound() -> None:
    records = tuple(_claim(subject=f"Subject {index}", value="x" * 90) for index in range(8))
    artifact, claims = await _artifact(*records)
    generation_id = IndexGenerationId.generate()
    chunker = MarkdownChunker(target_characters=350, overlap_characters=80, hard_max_characters=500)

    first = chunker.chunk(
        artifact,
        claims,
        generation_id=generation_id,
        generation_number=3,
        embedding_model="test/hash-embedding",
    )
    second = chunker.chunk(
        artifact,
        claims,
        generation_id=generation_id,
        generation_number=3,
        embedding_model="test/hash-embedding",
    )

    assert len(first) > 1
    assert first == second
    assert {claim.id for chunk in first for claim in claims if claim.id in chunk.claim_ids} == {
        claim.id for claim in claims
    }
    assert all(chunk.generation_number == 3 for chunk in first)
    assert all(len(chunk.text) <= 500 for chunk in first)


async def test_chunker_rejects_atomic_claim_over_hard_limit() -> None:
    artifact, claims = await _artifact(_claim(value="x" * 2000))
    with pytest.raises(ChunkingError, match="hard limit"):
        MarkdownChunker(
            target_characters=100,
            overlap_characters=20,
            hard_max_characters=200,
        ).chunk(
            artifact,
            claims,
            generation_id=IndexGenerationId.generate(),
            generation_number=1,
            embedding_model="test/hash-embedding",
        )
