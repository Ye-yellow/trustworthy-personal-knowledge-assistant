from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel

from trustworthy_kb.llm import (
    ModelGateway,
    ModelOutputValidationError,
    ModelPurpose,
    RoutedModel,
)


class Answer(BaseModel):
    answer: str


class StubRunnable:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.last_config: dict[str, Any] | None = None

    async def ainvoke(self, _messages: Any, config: dict[str, Any] | None = None) -> Any:
        self.last_config = config
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StubChatModel(StubRunnable):
    def __init__(self, result: Any, *, structured_result: Any = None) -> None:
        super().__init__(result)
        self.structured = StubRunnable(structured_result)
        self.structured_method: str | None = None

    def with_structured_output(self, _schema: type[BaseModel], *, method: str) -> StubRunnable:
        self.structured_method = method
        return self.structured

    async def astream(
        self,
        _messages: Any,
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[AIMessageChunk]:
        self.last_config = config
        yield AIMessageChunk(content="a")
        yield AIMessageChunk(content="b")


class StubRouter:
    def __init__(self, model: StubChatModel) -> None:
        self.model = model

    def route(self, purpose: ModelPurpose) -> RoutedModel:
        return RoutedModel(
            purpose=purpose,
            provider="sub2api",
            model_name="gpt-5.5",
            chat_model=self.model,
        )


@pytest.mark.asyncio
async def test_gateway_returns_standard_result_and_sanitizes_metadata() -> None:
    model = StubChatModel(
        AIMessage(
            content="grounded answer",
            response_metadata={"model_name": "gpt-5.5", "finish_reason": "stop"},
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        )
    )
    gateway = ModelGateway(StubRouter(model))  # type: ignore[arg-type]

    result = await gateway.invoke(
        "question",
        purpose=ModelPurpose.ANSWER_GENERATION,
        metadata={"safe": "value", "api_key": "must-not-leak"},
        tags=["test"],
    )

    assert result.text == "grounded answer"
    assert result.model == "gpt-5.5"
    assert result.finish_reason == "stop"
    assert result.total_tokens == 5
    assert model.last_config is not None
    assert model.last_config["metadata"]["safe"] == "value"
    assert "api_key" not in model.last_config["metadata"]
    assert model.last_config["metadata"]["purpose"] == "answer_generation"
    assert model.last_config["tags"] == ["trustworthy-kb", "answer_generation", "test"]


@pytest.mark.asyncio
async def test_gateway_validates_structured_output() -> None:
    model = StubChatModel(AIMessage(content="unused"), structured_result={"answer": "ok"})
    gateway = ModelGateway(StubRouter(model))  # type: ignore[arg-type]

    result = await gateway.invoke_structured(
        "question",
        schema=Answer,
        purpose=ModelPurpose.EVIDENCE_VERIFICATION,
    )

    assert result == Answer(answer="ok")
    assert model.structured_method == "json_mode"


@pytest.mark.asyncio
async def test_gateway_rejects_invalid_structured_output() -> None:
    model = StubChatModel(AIMessage(content="unused"), structured_result={"wrong": "shape"})
    gateway = ModelGateway(StubRouter(model))  # type: ignore[arg-type]

    with pytest.raises(ModelOutputValidationError):
        await gateway.invoke_structured(
            "question",
            schema=Answer,
            purpose=ModelPurpose.CLAIM_EXTRACTION,
        )


@pytest.mark.asyncio
async def test_gateway_streams_text_chunks() -> None:
    model = StubChatModel(AIMessage(content="unused"))
    gateway = ModelGateway(StubRouter(model))  # type: ignore[arg-type]

    chunks = [
        chunk
        async for chunk in gateway.stream(
            "question",
            purpose=ModelPurpose.ANSWER_GENERATION,
        )
    ]

    assert chunks == ["a", "b"]
