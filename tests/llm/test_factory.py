from __future__ import annotations

from typing import Any

import pytest

from trustworthy_kb.config import LLMSettings
from trustworthy_kb.llm import ModelConfigurationError, factory


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
    assert captured["api_key"] == "secret"
    assert "base_url" not in captured
    assert "use_responses_api" not in captured


def test_factory_allows_local_provider_without_api_key(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_init_chat_model(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "init_chat_model", fake_init_chat_model)
    settings = LLMSettings(provider="ollama", model="llama-test")

    factory.create_chat_model(settings, model_name=settings.model)

    assert captured["model_provider"] == "ollama"
    assert "api_key" not in captured
    assert "use_responses_api" not in captured


def test_factory_sanitizes_provider_configuration_errors(monkeypatch: Any) -> None:
    def fake_init_chat_model(**_kwargs: Any) -> object:
        raise ValueError("contains factory-secret")

    monkeypatch.setattr(factory, "init_chat_model", fake_init_chat_model)
    settings = LLMSettings(api_key="factory-secret")

    with pytest.raises(ModelConfigurationError) as exc_info:
        factory.create_chat_model(settings, model_name=settings.model)

    assert "factory-secret" not in str(exc_info.value)
    assert "provider=sub2api" in str(exc_info.value)
