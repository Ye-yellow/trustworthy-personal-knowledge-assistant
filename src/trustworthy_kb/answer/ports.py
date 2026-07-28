"""Provider-neutral dependencies for the trusted answer service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

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


__all__ = ["StructuredAnswerModelGateway"]
