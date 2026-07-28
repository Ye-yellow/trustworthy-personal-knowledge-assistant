from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trustworthy_kb.domain import (
    ClaimId,
    ClaimStatus,
    ClaimType,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeChangeId,
    KnowledgeNoteId,
    Sensitivity,
    SourceId,
    SourceVersionId,
)
from trustworthy_kb.publication.adapters import (
    DeterministicHashEmbedding,
    InMemoryVectorIndex,
)
from trustworthy_kb.publication.chunking import MarkdownChunker
from trustworthy_kb.publication.contracts import (
    CurationClaim,
    CurationGroup,
    CurationPlan,
    ExpectedPublication,
    ReconciliationSeverity,
)
from trustworthy_kb.publication.curation import CuratedMarkdownRenderer
from trustworthy_kb.publication.indexing import GenerationIndexer
from trustworthy_kb.publication.reconciliation import PublicationReconciler
from trustworthy_kb.publication.vault import AtomicVaultPublisher


async def test_reconciler_repairs_index_drift_then_reports_healthy(tmp_path: Path) -> None:
    expected, vault = await _expected_publication(tmp_path)
    embedding = DeterministicHashEmbedding()
    index = InMemoryVectorIndex()
    reconciler = PublicationReconciler(
        vault=vault,
        index=index,
        indexer=GenerationIndexer(embedding, index),
    )

    repaired = await reconciler.reconcile((expected,))
    healthy = await reconciler.reconcile((expected,))

    assert repaired.findings[0].severity is ReconciliationSeverity.REPAIRABLE
    assert repaired.findings[0].code == "index_repaired"
    assert repaired.findings[0].repaired is True
    assert healthy.findings[0].severity is ReconciliationSeverity.HEALTHY
    assert healthy.blocked is False


async def test_reconciler_blocks_tampered_vault_and_does_not_repair_index(
    tmp_path: Path,
) -> None:
    expected, vault = await _expected_publication(tmp_path)
    target = tmp_path / "vault" / expected.final_relative_path
    target.write_text(target.read_text(encoding="utf-8") + "manual change\n", encoding="utf-8")
    embedding = DeterministicHashEmbedding()
    index = InMemoryVectorIndex()
    reconciler = PublicationReconciler(
        vault=vault,
        index=index,
        indexer=GenerationIndexer(embedding, index),
    )

    report = await reconciler.reconcile((expected,))

    assert report.blocked is True
    assert report.findings[0].code == "vault_verification_failed"
    assert (
        await index.list_probes_for_version(
            expected.generation_number,
            expected.artifact.curated_version_id,
        )
        == ()
    )


async def _expected_publication(
    tmp_path: Path,
) -> tuple[ExpectedPublication, AtomicVaultPublisher]:
    now = datetime.now(UTC)
    claim = CurationClaim(
        id=ClaimId.generate(),
        claim_type=ClaimType.FACT,
        subject="Milvus",
        predicate="supports",
        object_json={"value": "hybrid retrieval"},
        status=ClaimStatus.VERIFIED,
        sensitivity=Sensitivity.PRIVATE,
    )
    artifact = CuratedMarkdownRenderer().render(
        note_id=KnowledgeNoteId.generate(),
        curated_version_id=CuratedVersionId.generate(),
        based_on_change_id=KnowledgeChangeId.generate(),
        version_number=1,
        plan=CurationPlan(
            title="Milvus",
            groups=(CurationGroup(heading="Search", claim_ids=(claim.id,)),),
        ),
        claims=(claim,),
        source_ids=(SourceId.generate(),),
        source_version_ids=(SourceVersionId.generate(),),
        model_name="test/planner",
        prompt_version="v1",
        quality_policy_version="v1",
        created_at=now,
    )
    generation_id = IndexGenerationId.generate()
    chunks = MarkdownChunker().chunk(
        artifact,
        (claim,),
        generation_id=generation_id,
        generation_number=1,
        embedding_model="test/hash-embedding",
    )
    root = tmp_path / "vault"
    root.mkdir()
    vault = AtomicVaultPublisher(root)
    await vault.stage(artifact)
    final_path = await vault.publish(artifact, "40-Concepts/Milvus.md")
    return (
        ExpectedPublication(
            artifact=artifact,
            final_relative_path=final_path,
            generation_number=1,
            chunks=chunks,
        ),
        vault,
    )
