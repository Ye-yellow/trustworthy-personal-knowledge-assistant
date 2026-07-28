"""Hybrid retrieval with fail-closed control-plane reconciliation."""

from __future__ import annotations

from collections.abc import Sequence

from trustworthy_kb.domain import CuratedVersionId, IndexGenerationId, KnowledgeNoteId
from trustworthy_kb.publication.contracts import (
    RerankItem,
    RetrievalHit,
    RetrievalMode,
    RetrievalQuery,
    RetrievalResult,
    VectorSearchHit,
    VectorSearchRequest,
)
from trustworthy_kb.publication.errors import RetrievalError
from trustworthy_kb.publication.ports import (
    CurrentVersionResolver,
    EmbeddingGateway,
    RerankerGateway,
    VectorIndexGateway,
)


class HybridRetriever:
    """Combine index ranking with authoritative SQLite current-version checks."""

    def __init__(
        self,
        *,
        embedding: EmbeddingGateway,
        index: VectorIndexGateway,
        current_versions: CurrentVersionResolver,
        reranker: RerankerGateway | None = None,
        allow_bm25_only: bool = False,
        rrf_k: int = 60,
    ) -> None:
        if rrf_k < 1:
            raise ValueError("RRF k must be positive")
        self._embedding = embedding
        self._index = index
        self._current_versions = current_versions
        self._reranker = reranker
        self._allow_bm25_only = allow_bm25_only
        self._rrf_k = rrf_k

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        generation_id: IndexGenerationId,
        generation_number: int,
    ) -> RetrievalResult:
        """Retrieve only hits still active in the authoritative control plane."""

        degraded = False
        mode = RetrievalMode.HYBRID
        try:
            dense = await self._embedding.embed_query(query.text)
            if len(dense) != self._embedding.dimension:
                raise ValueError("invalid query vector")
        except Exception:
            if not self._allow_bm25_only:
                raise RetrievalError("query embedding failed") from None
            dense = ()
            degraded = True
            mode = RetrievalMode.BM25_ONLY
        try:
            candidates = await self._index.hybrid_search(
                VectorSearchRequest(
                    query=query,
                    dense=dense,
                    generation_number=generation_number,
                    rrf_k=self._rrf_k,
                )
            )
        except Exception:
            raise RetrievalError("hybrid index search failed") from None
        current = await self._resolve_current(candidates)
        active = tuple(
            item
            for item in candidates
            if current.get(item.chunk.note_id) == (item.chunk.curated_version_id, generation_id)
        )
        ranked, rerank_degraded = await self._rerank(query, active)
        return RetrievalResult(
            hits=ranked[: query.top_k],
            mode=mode,
            degraded=degraded or rerank_degraded,
            generation_id=generation_id,
        )

    async def _resolve_current(
        self, candidates: Sequence[VectorSearchHit]
    ) -> dict[KnowledgeNoteId, tuple[CuratedVersionId, IndexGenerationId]]:
        note_ids = tuple(dict.fromkeys(item.chunk.note_id for item in candidates))
        if not note_ids:
            return {}
        try:
            values = await self._current_versions.resolve_current_versions(note_ids)
        except Exception:
            raise RetrievalError("current-version control-plane lookup failed") from None
        return {note_id: value for note_id, value in values.items()}

    async def _rerank(
        self,
        query: RetrievalQuery,
        candidates: Sequence[VectorSearchHit],
    ) -> tuple[tuple[RetrievalHit, ...], bool]:
        baseline = tuple(
            RetrievalHit(chunk=item.chunk, retrieval_score=item.score) for item in candidates
        )
        if self._reranker is None or not candidates:
            return baseline, self._reranker is None
        inputs = tuple(
            RerankItem(chunk_id=item.chunk.chunk_id, text=item.chunk.text, score=item.score)
            for item in candidates
        )
        try:
            reranked = await self._reranker.rerank(
                query.text,
                inputs,
                top_k=min(query.candidate_k, len(inputs)),
            )
            candidate_by_id = {item.chunk.chunk_id: item for item in candidates}
            if len({item.chunk_id for item in reranked}) != len(reranked) or any(
                item.chunk_id not in candidate_by_id for item in reranked
            ):
                raise ValueError("reranker returned an invalid candidate set")
            return (
                tuple(
                    RetrievalHit(
                        chunk=candidate_by_id[item.chunk_id].chunk,
                        retrieval_score=candidate_by_id[item.chunk_id].score,
                        rerank_score=item.score,
                    )
                    for item in reranked
                ),
                False,
            )
        except Exception:
            return baseline, True


__all__ = ["HybridRetriever"]
