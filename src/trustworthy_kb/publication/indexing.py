"""Generation-aware indexing orchestration with strong read-back verification."""

from __future__ import annotations

from collections.abc import Sequence

from trustworthy_kb.publication.contracts import IndexedChunk, KnowledgeChunk
from trustworthy_kb.publication.errors import IndexingError
from trustworthy_kb.publication.ports import EmbeddingGateway, VectorIndexGateway


class GenerationIndexer:
    """Embed and idempotently index complete Chunk sets for one generation."""

    def __init__(
        self,
        embedding: EmbeddingGateway,
        index: VectorIndexGateway,
    ) -> None:
        self._embedding = embedding
        self._index = index

    @property
    def embedding_model(self) -> str:
        return self._embedding.model_name

    @property
    def embedding_dimension(self) -> int:
        return self._embedding.dimension

    async def index(self, chunks: Sequence[KnowledgeChunk]) -> int:
        """Upsert all chunks and prove their identity through the strong-read port."""

        if not chunks:
            raise IndexingError("cannot index an empty Chunk set")
        generation_numbers = {item.generation_number for item in chunks}
        generation_ids = {item.generation_id for item in chunks}
        models = {item.embedding_model for item in chunks}
        if (
            len(generation_numbers) != 1
            or len(generation_ids) != 1
            or models != {self._embedding.model_name}
        ):
            raise IndexingError("Chunk set does not belong to one configured generation")
        generation_number = next(iter(generation_numbers))
        await self._index.ensure_generation(
            generation_number=generation_number,
            embedding_dimension=self._embedding.dimension,
        )
        try:
            vectors = await self._embedding.embed_documents([item.text for item in chunks])
        except Exception:
            raise IndexingError("document embedding failed") from None
        if len(vectors) != len(chunks) or any(
            len(vector) != self._embedding.dimension for vector in vectors
        ):
            raise IndexingError("embedding provider returned an invalid vector batch")
        indexed = tuple(
            IndexedChunk(chunk=chunk, dense=vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        try:
            await self._index.upsert(generation_number, indexed)
            probes = await self._index.fetch_probes(
                generation_number,
                [item.chunk_id for item in chunks],
            )
        except Exception:
            raise IndexingError("index upsert or strong verification failed") from None
        expected = {(item.chunk_id, item.curated_version_id, item.content_hash) for item in chunks}
        actual = {(item.chunk_id, item.curated_version_id, item.content_hash) for item in probes}
        if actual != expected or len(probes) != len(chunks):
            raise IndexingError("strong index read-back did not match the Chunk set")
        return len(chunks)

    async def delete(self, chunks: Sequence[KnowledgeChunk]) -> int:
        """Delete an explicit stable-ID set and verify that no probe remains."""

        if not chunks:
            return 0
        generations = {item.generation_number for item in chunks}
        if len(generations) != 1:
            raise IndexingError("delete set spans multiple index generations")
        generation_number = next(iter(generations))
        ids = [item.chunk_id for item in chunks]
        try:
            await self._index.delete_chunks(generation_number, ids)
            remaining = await self._index.fetch_probes(generation_number, ids)
        except Exception:
            raise IndexingError("index deletion verification failed") from None
        if remaining:
            raise IndexingError("deleted Chunk IDs remain visible in the index")
        return len(chunks)


__all__ = ["GenerationIndexer"]
