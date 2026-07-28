from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
    InMemoryCurrentVersionResolver,
    InMemoryVectorIndex,
    TokenOverlapReranker,
)
from trustworthy_kb.publication.chunking import MarkdownChunker
from trustworthy_kb.publication.contracts import (
    CurationClaim,
    CurationGroup,
    CurationPlan,
    RetrievalMode,
    RetrievalQuery,
)
from trustworthy_kb.publication.curation import CuratedMarkdownRenderer
from trustworthy_kb.publication.errors import IndexingError, RetrievalError
from trustworthy_kb.publication.indexing import GenerationIndexer
from trustworthy_kb.publication.retrieval import HybridRetriever


def _chunks(*, freshness_at: datetime | None = None):
    now = datetime.now(UTC)
    claim = CurationClaim(
        id=ClaimId.generate(),
        claim_type=ClaimType.FACT,
        subject="Milvus",
        predicate="supports",
        object_json={"value": "dense sparse hybrid search"},
        status=ClaimStatus.VERIFIED,
        sensitivity=Sensitivity.PRIVATE,
        freshness_at=freshness_at,
    )
    note_id = KnowledgeNoteId.generate()
    version_id = CuratedVersionId.generate()
    artifact = CuratedMarkdownRenderer().render(
        note_id=note_id,
        curated_version_id=version_id,
        based_on_change_id=KnowledgeChangeId.generate(),
        version_number=1,
        plan=CurationPlan(
            title="Milvus",
            groups=(CurationGroup(heading="Search", claim_ids=(claim.id,)),),
        ),
        claims=(claim,),
        source_ids=(SourceId.generate(),),
        source_version_ids=(SourceVersionId.generate(),),
        model_name="gpt-5.5",
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
    return chunks, note_id, version_id, generation_id


async def test_generation_indexer_and_hybrid_retriever_filter_stale_versions() -> None:
    chunks, note_id, version_id, generation_id = _chunks()
    embedding = DeterministicHashEmbedding()
    index = InMemoryVectorIndex()
    assert await GenerationIndexer(embedding, index).index(chunks) == len(chunks)
    resolver = InMemoryCurrentVersionResolver({note_id: (version_id, generation_id)})
    retriever = HybridRetriever(
        embedding=embedding,
        index=index,
        current_versions=resolver,
        reranker=TokenOverlapReranker(),
    )
    query = RetrievalQuery(
        text="hybrid sparse search",
        at=datetime.now(UTC),
        max_sensitivity=Sensitivity.PRIVATE,
    )

    result = await retriever.retrieve(
        query,
        generation_id=generation_id,
        generation_number=1,
    )
    assert result.hits
    assert result.mode is RetrievalMode.HYBRID
    assert result.degraded is False

    resolver.values[note_id] = (CuratedVersionId.generate(), generation_id)
    stale = await retriever.retrieve(
        query,
        generation_id=generation_id,
        generation_number=1,
    )
    assert stale.hits == ()


async def test_retrieval_filters_expired_chunks_and_marks_missing_reranker_degraded() -> None:
    chunks, note_id, version_id, generation_id = _chunks(
        freshness_at=datetime.now(UTC) - timedelta(days=1)
    )
    embedding = DeterministicHashEmbedding()
    index = InMemoryVectorIndex()
    await GenerationIndexer(embedding, index).index(chunks)
    retriever = HybridRetriever(
        embedding=embedding,
        index=index,
        current_versions=InMemoryCurrentVersionResolver({note_id: (version_id, generation_id)}),
    )

    result = await retriever.retrieve(
        RetrievalQuery(
            text="Milvus",
            at=datetime.now(UTC),
            max_sensitivity=Sensitivity.PRIVATE,
        ),
        generation_id=generation_id,
        generation_number=1,
    )
    assert result.hits == ()
    assert result.degraded is True


class _BrokenEmbedding(DeterministicHashEmbedding):
    async def embed_query(self, text: str) -> tuple[float, ...]:
        raise RuntimeError("synthetic failure")


async def test_embedding_failure_is_fail_closed_or_explicit_bm25_degradation() -> None:
    chunks, note_id, version_id, generation_id = _chunks()
    index_embedding = DeterministicHashEmbedding()
    index = InMemoryVectorIndex()
    await GenerationIndexer(index_embedding, index).index(chunks)
    resolver = InMemoryCurrentVersionResolver({note_id: (version_id, generation_id)})
    query = RetrievalQuery(
        text="sparse search",
        at=datetime.now(UTC),
        max_sensitivity=Sensitivity.PRIVATE,
    )

    with pytest.raises(RetrievalError, match="embedding"):
        await HybridRetriever(
            embedding=_BrokenEmbedding(),
            index=index,
            current_versions=resolver,
        ).retrieve(query, generation_id=generation_id, generation_number=1)

    degraded = await HybridRetriever(
        embedding=_BrokenEmbedding(),
        index=index,
        current_versions=resolver,
        allow_bm25_only=True,
    ).retrieve(query, generation_id=generation_id, generation_number=1)
    assert degraded.hits
    assert degraded.mode is RetrievalMode.BM25_ONLY
    assert degraded.degraded is True


async def test_indexer_rejects_mismatched_embedding_model() -> None:
    chunks, *_ = _chunks()
    wrong = tuple(item.model_copy(update={"embedding_model": "different/model"}) for item in chunks)
    with pytest.raises(IndexingError, match="generation"):
        await GenerationIndexer(DeterministicHashEmbedding(), InMemoryVectorIndex()).index(wrong)
