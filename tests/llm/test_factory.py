from __future__ import annotations

from typing import Any

from trustworthy_kb.config import LLMSettings
from trustworthy_kb.llm import factory


def test_factory_maps_sub2api_to_openai_compatible_chat_model(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_init_chat_model(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(factory, "init_chat_model", fake_init_chat_model)
    settings = LLMSettings(api_key="factory-secret")

    result = factory.create_chat_model(settings, model_name="gpt-5.6-terra")

    assert result is sentinel
    assert captured == {
        "model": "gpt-5.6-terra",
        "model_provider": "openai",
        "base_url": "http://localhost:8080/v1",
        "api_key": "factory-secret",
        "timeout": 120.0,
        "max_retries": 2,
        "use_responses_api": False,
    }


def test_factory_allows_other_langchain_providers(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_init_chat_model(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "init_chat_model", fake_init_chat_model)
    settings = LLMSettings(provider="anthropic", model="claude-test", api_key="secret")

    factory.create_chat_model(settings, model_name=settings.model)

    assert captured["model_provider"] == "anthropic"
    assert "base_url" not in captured
    assert "use_responses_api" not in captured
