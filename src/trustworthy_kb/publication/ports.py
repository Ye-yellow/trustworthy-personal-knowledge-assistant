"""Provider-neutral ports used by publication business logic."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from trustworthy_kb.domain import CuratedVersionId, IndexGenerationId, KnowledgeNoteId
from trustworthy_kb.publication.contracts import (
    CurationClaim,
    CurationPlan,
    IndexedChunk,
    IndexProbe,
    RerankItem,
    VectorSearchHit,
    VectorSearchRequest,
)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class CurationPlanner(Protocol):
    async def plan(self, claims: Sequence[CurationClaim]) -> CurationPlan: ...


class StructuredModelGateway(Protocol):
    async def invoke_structured(
        self,
        messages: str | Sequence[BaseMessage],
        *,
        schema: type[SchemaT],
        purpose: Any,
        metadata: Mapping[str, Any] | None = None,
        tags: Sequence[str] = (),
    ) -> SchemaT: ...


class EmbeddingGateway(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...

    async def embed_query(self, text: str) -> tuple[float, ...]: ...


class VectorIndexGateway(Protocol):
    async def ensure_generation(
        self,
        *,
        generation_number: int,
        embedding_dimension: int,
    ) -> None: ...

    async def upsert(self, generation_number: int, chunks: Sequence[IndexedChunk]) -> None: ...

    async def fetch_probes(
        self, generation_number: int, chunk_ids: Sequence[str]
    ) -> tuple[IndexProbe, ...]: ...

    async def hybrid_search(self, request: VectorSearchRequest) -> tuple[VectorSearchHit, ...]: ...

    async def delete_chunks(self, generation_number: int, chunk_ids: Sequence[str]) -> None: ...

    async def list_probes_for_version(
        self, generation_number: int, curated_version_id: CuratedVersionId
    ) -> tuple[IndexProbe, ...]: ...


class RerankerGateway(Protocol):
    @property
    def model_name(self) -> str: ...

    async def rerank(
        self, query: str, candidates: Sequence[RerankItem], *, top_k: int
    ) -> tuple[RerankItem, ...]: ...


class CurrentVersionResolver(Protocol):
    async def resolve_current_versions(
        self, note_ids: Sequence[KnowledgeNoteId]
    ) -> Mapping[KnowledgeNoteId, tuple[CuratedVersionId, IndexGenerationId]]: ...


class VaultVerificationGateway(Protocol):
    async def verify(self, relative_path: str, *, expected_hash: str) -> Mapping[str, object]: ...


__all__ = [
    "CurationPlanner",
    "CurrentVersionResolver",
    "EmbeddingGateway",
    "RerankerGateway",
    "StructuredModelGateway",
    "VaultVerificationGateway",
    "VectorIndexGateway",
]
