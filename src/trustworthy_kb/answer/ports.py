"""Provider-neutral dependencies for the trusted answer service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from trustworthy_kb.answer.contracts import (
    AnswerDraft,
    AnswerEvidence,
    AnswerRequest,
    CitationVerificationOutput,
    QueryPlan,
)
from trustworthy_kb.domain import IndexGenerationId
from trustworthy_kb.publication.contracts import RetrievalQuery, RetrievalResult

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StructuredAnswerModelGateway(Protocol):
    async def invoke_structured(
        self,
        messages: str | Sequence[BaseMessage],
        *,
        schema: type[SchemaT],
        purpose: Any,
        metadata: Mapping[str, Any] | None = None,
        tags: Sequence[str] = (),
    ) -> SchemaT: ...


class AnswerPlanningGateway(Protocol):
    async def plan(self, request: AnswerRequest) -> QueryPlan: ...


class AnswerRetrievalGateway(Protocol):
    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        generation_id: IndexGenerationId,
        generation_number: int,
    ) -> RetrievalResult: ...


class AnswerEvidenceResolver(Protocol):
    async def resolve(self, result: RetrievalResult) -> tuple[AnswerEvidence, ...]: ...


class AnswerGenerationGateway(Protocol):
    async def generate(
        self,
        plan: QueryPlan,
        evidence: Sequence[AnswerEvidence],
    ) -> AnswerDraft: ...


class AnswerVerificationGateway(Protocol):
    async def verify(
        self,
        draft: AnswerDraft,
        evidence: Sequence[AnswerEvidence],
    ) -> CitationVerificationOutput: ...


__all__ = [
    "AnswerEvidenceResolver",
    "AnswerGenerationGateway",
    "AnswerPlanningGateway",
    "AnswerRetrievalGateway",
    "AnswerVerificationGateway",
    "StructuredAnswerModelGateway",
]
