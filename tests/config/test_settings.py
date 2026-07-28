from __future__ import annotations

import pytest
from pydantic import ValidationError

from trustworthy_kb.config import LLMSettings


def test_llm_settings_use_sub2api_defaults_and_redact_secret() -> None:
    settings = LLMSettings(api_key="unit-test-secret")

    assert settings.provider == "sub2api"
    assert settings.model == "gpt-5.5"
    assert settings.base_url == "http://localhost:8080/v1"
    assert settings.timeout_seconds == 120.0
    assert settings.max_retries == 2
    assert "unit-test-secret" not in repr(settings)
    assert "unit-test-secret" not in settings.model_dump_json()


def test_llm_settings_load_purpose_overrides_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTKB_LLM_API_KEY", "environment-secret")
    monkeypatch.setenv("TRUSTKB_LLM_EXTRACTOR_MODEL", "extractor-model")
    monkeypatch.setenv("TRUSTKB_LLM_VERIFIER_MODEL", "verifier-model")
    monkeypatch.setenv("TRUSTKB_LLM_CURATION_MODEL", "curation-model")
    monkeypatch.setenv("TRUSTKB_LLM_ANSWER_MODEL", "answer-model")

    settings = LLMSettings()

    assert settings.extractor_model == "extractor-model"
    assert settings.verifier_model == "verifier-model"
    assert settings.curation_model == "curation-model"
    assert settings.answer_model == "answer-model"


@pytest.mark.parametrize("api_key", ["", "   "])
def test_llm_settings_reject_empty_api_key(api_key: str) -> None:
    with pytest.raises(ValidationError):
        LLMSettings(api_key=api_key)


def test_non_sub2api_provider_does_not_inherit_local_base_url() -> None:
    settings = LLMSettings(provider="anthropic", model="claude-test", api_key="secret")

    assert settings.base_url is None
