"""Safe LangChain boundary exposed to business workflows."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, TypeVar

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError

from trustworthy_kb.llm.errors import (
    ModelOutputValidationError,
    ModelProviderError,
    map_model_exception,
)
from trustworthy_kb.llm.router import ModelRouter
from trustworthy_kb.llm.types import ModelPurpose, ModelResult, RoutedModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ModelInput = str | Sequence[BaseMessage]
_SENSITIVE_METADATA_PARTS = ("api_key", "apikey", "authorization", "password", "secret", "token")


class ModelGateway:
    """Invoke routed LangChain models with stable results and fail-closed errors."""

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def invoke(
        self,
        messages: ModelInput,
        *,
        purpose: ModelPurpose,
        metadata: Mapping[str, Any] | None = None,
        tags: Sequence[str] = (),
    ) -> ModelResult:
        """Invoke a model and normalize its standard response metadata."""

        selection = self._router.route(purpose)
        config = _runnable_config(selection, metadata, tags)
        try:
            message = await selection.chat_model.ainvoke(messages, config=config)
        except Exception as error:
            raise map_model_exception(
                error,
                provider=selection.provider,
                model=selection.model_name,
            ) from None
        if not isinstance(message, AIMessage):
            raise ModelProviderError(
                "model provider failed "
                f"(provider={selection.provider}, model={selection.model_name})"
            )
        return _model_result(message, selection)

    async def invoke_structured(
        self,
        messages: ModelInput,
        *,
        schema: type[SchemaT],
        purpose: ModelPurpose,
        metadata: Mapping[str, Any] | None = None,
        tags: Sequence[str] = (),
    ) -> SchemaT:
        """Invoke JSON mode and validate the result against ``schema``."""

        selection = self._router.route(purpose)
        config = _runnable_config(selection, metadata, tags)
        runnable = selection.chat_model.with_structured_output(schema, method="json_mode")
        try:
            raw_result = await runnable.ainvoke(messages, config=config)
            if isinstance(raw_result, schema):
                return raw_result
            return schema.model_validate(raw_result)
        except ValidationError:
            raise ModelOutputValidationError(
                "model output validation failed "
                f"(provider={selection.provider}, model={selection.model_name})"
            ) from None
        except Exception as error:
            raise map_model_exception(
                error,
                provider=selection.provider,
                model=selection.model_name,
            ) from None

    async def stream(
        self,
        messages: ModelInput,
        *,
        purpose: ModelPurpose,
        metadata: Mapping[str, Any] | None = None,
        tags: Sequence[str] = (),
    ) -> AsyncIterator[str]:
        """Yield non-empty text chunks from a routed model."""

        selection = self._router.route(purpose)
        config = _runnable_config(selection, metadata, tags)
        try:
            async for chunk in selection.chat_model.astream(messages, config=config):
                text = _content_text(chunk.content)
                if text:
                    yield text
        except Exception as error:
            raise map_model_exception(
                error,
                provider=selection.provider,
                model=selection.model_name,
            ) from None


def _runnable_config(
    selection: RoutedModel,
    metadata: Mapping[str, Any] | None,
    tags: Sequence[str],
) -> RunnableConfig:
    safe_metadata = {
        key: value
        for key, value in (metadata or {}).items()
        if not any(part in key.lower() for part in _SENSITIVE_METADATA_PARTS)
    }
    safe_metadata.update(
        {
            "provider": selection.provider,
            "model": selection.model_name,
            "purpose": selection.purpose.value,
        }
    )
    all_tags = list(
        dict.fromkeys(["trustworthy-kb", selection.purpose.value, *[str(tag) for tag in tags]])
    )
    return RunnableConfig(metadata=safe_metadata, tags=all_tags)


def _model_result(message: AIMessage, selection: RoutedModel) -> ModelResult:
    response_metadata = message.response_metadata or {}
    usage = message.usage_metadata
    if usage is None:
        input_tokens = output_tokens = total_tokens = 0
    else:
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
    return ModelResult(
        text=_content_text(message.content),
        provider=selection.provider,
        model=str(response_metadata.get("model_name") or selection.model_name),
        purpose=selection.purpose,
        finish_reason=_optional_text(response_metadata.get("finish_reason")),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        response_id=message.id,
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None
