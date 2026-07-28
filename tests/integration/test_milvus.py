from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from trustworthy_kb.domain import (
    ClaimId,
    ClaimStatus,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeNoteId,
    Sensitivity,
)
from trustworthy_kb.publication.adapters.milvus import MilvusVectorIndex
from trustworthy_kb.publication.contracts import (
    IndexedChunk,
    KnowledgeChunk,
    RetrievalQuery,
    VectorSearchRequest,
)


@pytest.mark.integration
async def test_real_milvus_generation_supports_strong_probes_and_hybrid_search() -> None:
    pymilvus = pytest.importorskip("pymilvus")
    uri = "http://127.0.0.1:19530"
    prefix = f"trustkb_it_{uuid4().hex[:12]}_g"
    adapter = MilvusVectorIndex(
        uri=uri,
        collection_prefix=prefix,
        consistency="Strong",
        timeout_seconds=60,
    )
    collection = adapter.collection_name(1)
    cleanup = pymilvus.MilvusClient(uri=uri, timeout=60)
    now = datetime.now(UTC).replace(microsecond=0)
    version_id = CuratedVersionId.generate()
    generation_id = IndexGenerationId.generate()
    indexed = (
        _indexed_chunk(
            text="Trustworthy knowledge validates facts with evidence lineage.",
            dense=(1.0, 0.0, 0.0),
            ordinal=0,
            version_id=version_id,
            generation_id=generation_id,
            now=now,
        ),
        _indexed_chunk(
            text="A plain memo records unrelated errands.",
            dense=(0.0, 1.0, 0.0),
            ordinal=1,
            version_id=version_id,
            generation_id=generation_id,
            now=now,
        ),
    )

    try:
        await adapter.ensure_generation(generation_number=1, embedding_dimension=3)
        await adapter.upsert(1, indexed)

        probes = await adapter.fetch_probes(1, [item.chunk.chunk_id for item in indexed])
        assert {item.chunk_id for item in probes} == {item.chunk.chunk_id for item in indexed}
        assert len(await adapter.list_probes_for_version(1, version_id)) == 2

        query = RetrievalQuery(
            text="evidence lineage",
            top_k=2,
            candidate_k=2,
            at=now,
            max_sensitivity=Sensitivity.PRIVATE,
        )
        hybrid = await adapter.hybrid_search(
            VectorSearchRequest(
                query=query,
                dense=(1.0, 0.0, 0.0),
                generation_number=1,
            )
        )
        lexical = await adapter.hybrid_search(
            VectorSearchRequest(query=query, dense=(), generation_number=1)
        )
        assert hybrid[0].chunk.chunk_id == indexed[0].chunk.chunk_id
        assert lexical[0].chunk.chunk_id == indexed[0].chunk.chunk_id

        await adapter.delete_chunks(1, (indexed[1].chunk.chunk_id,))
        assert await adapter.fetch_probes(1, (indexed[1].chunk.chunk_id,)) == ()
    finally:
        await adapter.close()
        if cleanup.has_collection(collection_name=collection, timeout=60):
            cleanup.drop_collection(collection_name=collection, timeout=60)
        cleanup.close()


def _indexed_chunk(
    *,
    text: str,
    dense: tuple[float, ...],
    ordinal: int,
    version_id: CuratedVersionId,
    generation_id: IndexGenerationId,
    now: datetime,
) -> IndexedChunk:
    return IndexedChunk(
        chunk=KnowledgeChunk(
            chunk_id=f"{ordinal + 1}" * 64,
            note_id=KnowledgeNoteId.generate(),
            curated_version_id=version_id,
            claim_ids=(ClaimId.generate(),),
            text=text,
            heading_path=("可信知识",),
            ordinal=ordinal,
            quality_status=ClaimStatus.VERIFIED,
            sensitivity=Sensitivity.PRIVATE,
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=1),
            freshness_at=now + timedelta(days=30),
            generation_id=generation_id,
            generation_number=1,
            embedding_model="synthetic/integration",
            chunker_version="markdown-v1",
            content_hash=f"{ordinal + 3}" * 64,
        ),
        dense=dense,
    )
