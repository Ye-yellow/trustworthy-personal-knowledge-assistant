"""Deterministic test adapters; never selected by production configuration."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

from trustworthy_kb.domain import CuratedVersionId, IndexGenerationId, KnowledgeNoteId, Sensitivity
from trustworthy_kb.publication.contracts import (
    IndexedChunk,
    IndexProbe,
    RerankItem,
    VectorSearchHit,
    VectorSearchRequest,
    utc_milliseconds,
)

_TOKEN = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)
_SENSITIVITY_ORDER = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.RESTRICTED: 1,
    Sensitivity.PRIVATE: 2,
}


class DeterministicHashEmbedding:
    """Small normalized hash embedding for unit and offline integration tests."""

    def __init__(self, *, dimension: int = 32, model_name: str = "test/hash-embedding") -> None:
        if dimension < 2 or not model_name.strip():
            raise ValueError("test embedding configuration is invalid")
        self._dimension = dimension
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(_hash_vector(text, self._dimension) for text in texts)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return _hash_vector(text, self._dimension)


class InMemoryVectorIndex:
    """In-process dense + token ranking implementation for deterministic tests."""

    def __init__(self) -> None:
        self._dimensions: dict[int, int] = {}
        self._rows: dict[int, dict[str, IndexedChunk]] = {}

    async def ensure_generation(self, *, generation_number: int, embedding_dimension: int) -> None:
        current = self._dimensions.get(generation_number)
        if current is not None and current != embedding_dimension:
            raise ValueError("index generation embedding dimension changed")
        self._dimensions[generation_number] = embedding_dimension
        self._rows.setdefault(generation_number, {})

    async def upsert(self, generation_number: int, chunks: Sequence[IndexedChunk]) -> None:
        dimension = self._dimensions[generation_number]
        rows = self._rows[generation_number]
        for item in chunks:
            if item.chunk.generation_number != generation_number or len(item.dense) != dimension:
                raise ValueError("indexed Chunk does not match its generation")
            rows[item.chunk.chunk_id] = item

    async def fetch_probes(
        self, generation_number: int, chunk_ids: Sequence[str]
    ) -> tuple[IndexProbe, ...]:
        rows = self._rows.get(generation_number, {})
        return tuple(
            IndexProbe(
                chunk_id=item.chunk.chunk_id,
                curated_version_id=item.chunk.curated_version_id,
                content_hash=item.chunk.content_hash,
            )
            for chunk_id in dict.fromkeys(chunk_ids)
            if (item := rows.get(chunk_id)) is not None
        )

    async def hybrid_search(self, request: VectorSearchRequest) -> tuple[VectorSearchHit, ...]:
        rows = [
            item
            for item in self._rows.get(request.generation_number, {}).values()
            if _allowed(item, request)
        ]
        dense_rank = (
            sorted(rows, key=lambda item: _cosine(request.dense, item.dense), reverse=True)
            if request.dense
            else []
        )
        query_tokens = Counter(_tokens(request.query.text))
        sparse_rank = sorted(
            rows,
            key=lambda item: _token_score(query_tokens, Counter(_tokens(item.chunk.text))),
            reverse=True,
        )
        scores: defaultdict[str, float] = defaultdict(float)
        by_id = {item.chunk.chunk_id: item for item in rows}
        for ranking in (dense_rank, sparse_rank):
            for rank, item in enumerate(ranking[: request.query.candidate_k], start=1):
                scores[item.chunk.chunk_id] += 1 / (request.rrf_k + rank)
        ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
        return tuple(
            VectorSearchHit(chunk=by_id[chunk_id].chunk, score=scores[chunk_id])
            for chunk_id in ordered[: request.query.candidate_k]
        )

    async def delete_chunks(self, generation_number: int, chunk_ids: Sequence[str]) -> None:
        rows = self._rows.get(generation_number, {})
        for chunk_id in chunk_ids:
            rows.pop(chunk_id, None)

    async def list_probes_for_version(
        self, generation_number: int, curated_version_id: CuratedVersionId
    ) -> tuple[IndexProbe, ...]:
        rows = self._rows.get(generation_number, {})
        return tuple(
            IndexProbe(
                chunk_id=item.chunk.chunk_id,
                curated_version_id=item.chunk.curated_version_id,
                content_hash=item.chunk.content_hash,
            )
            for item in sorted(rows.values(), key=lambda value: value.chunk.chunk_id)
            if item.chunk.curated_version_id == curated_version_id
        )


class InMemoryCurrentVersionResolver:
    """Mutable control-plane view for unit tests."""

    def __init__(
        self,
        values: Mapping[KnowledgeNoteId, tuple[CuratedVersionId, IndexGenerationId]] | None = None,
    ) -> None:
        self.values = dict(values or {})

    async def resolve_current_versions(
        self, note_ids: Sequence[KnowledgeNoteId]
    ) -> Mapping[KnowledgeNoteId, tuple[CuratedVersionId, IndexGenerationId]]:
        return {note_id: self.values[note_id] for note_id in note_ids if note_id in self.values}


class TokenOverlapReranker:
    """Deterministic lexical reranker for tests."""

    @property
    def model_name(self) -> str:
        return "test/token-overlap"

    async def rerank(
        self, query: str, candidates: Sequence[RerankItem], *, top_k: int
    ) -> tuple[RerankItem, ...]:
        query_tokens = Counter(_tokens(query))
        ranked = sorted(
            candidates,
            key=lambda item: (
                -_token_score(query_tokens, Counter(_tokens(item.text))),
                -item.score,
                item.chunk_id,
            ),
        )
        return tuple(
            RerankItem(
                chunk_id=item.chunk_id,
                text=item.text,
                score=_token_score(query_tokens, Counter(_tokens(item.text))),
            )
            for item in ranked[:top_k]
        )


def _hash_vector(text: str, dimension: int) -> tuple[float, ...]:
    values = [0.0] * dimension
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        values[index] += -1.0 if digest[4] & 1 else 1.0
    norm = math.sqrt(sum(item * item for item in values))
    if norm == 0:
        values[0] = 1.0
        norm = 1.0
    return tuple(item / norm for item in values)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(item.casefold() for item in _TOKEN.findall(text))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _token_score(query: Counter[str], document: Counter[str]) -> float:
    return float(sum(min(count, document[token]) for token, count in query.items()))


def _allowed(item: IndexedChunk, request: VectorSearchRequest) -> bool:
    chunk = item.chunk
    if chunk.quality_status not in request.query.allowed_quality_statuses:
        return False
    if _SENSITIVITY_ORDER[chunk.sensitivity] > _SENSITIVITY_ORDER[request.query.max_sensitivity]:
        return False
    at_ms = utc_milliseconds(request.query.at)
    if chunk.valid_from is not None and utc_milliseconds(chunk.valid_from) > at_ms:
        return False
    if chunk.valid_to is not None and utc_milliseconds(chunk.valid_to) < at_ms:
        return False
    return (
        request.query.allow_stale
        or chunk.freshness_at is None
        or utc_milliseconds(chunk.freshness_at) >= at_ms
    )


__all__ = [
    "DeterministicHashEmbedding",
    "InMemoryCurrentVersionResolver",
    "InMemoryVectorIndex",
    "TokenOverlapReranker",
]
