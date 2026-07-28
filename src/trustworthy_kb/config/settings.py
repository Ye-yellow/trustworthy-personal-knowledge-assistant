"""Validated runtime settings for language models."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import AnyHttpUrl, Field, SecretStr, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_API_KEY_OPTIONAL_PROVIDERS = frozenset({"ollama"})


class LLMSettings(BaseSettings):
    """Language-model settings loaded from ``TRUSTKB_LLM_*`` variables."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTKB_LLM_",
        case_sensitive=False,
        extra="ignore",
    )

    provider: str = "sub2api"
    model: str = "gpt-5.5"
    base_url: str | None = None
    api_key: SecretStr | None = None
    timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 120.0
    max_retries: Annotated[int, Field(ge=0, le=10)] = 2
    extractor_model: str | None = None
    verifier_model: str | None = None
    curation_model: str | None = None
    answer_model: str | None = None

    @field_validator("provider", "model", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator(
        "extractor_model",
        "verifier_model",
        "curation_model",
        "answer_model",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        normalized = value.get_secret_value().strip()
        if not normalized:
            raise ValueError("API key must not be empty")
        return SecretStr(normalized)

    @model_validator(mode="after")
    def _normalize_provider_and_base_url(self) -> Self:
        self.provider = self.provider.lower()
        if self.api_key is None and self.provider not in _API_KEY_OPTIONAL_PROVIDERS:
            raise ValueError(f"API key is required for provider '{self.provider}'")
        if self.provider == "sub2api" and self.base_url is None:
            self.base_url = "http://localhost:8080/v1"
        if self.base_url is not None:
            normalized = self.base_url.strip().rstrip("/")
            self.base_url = str(_HTTP_URL_ADAPTER.validate_python(normalized)).rstrip("/")
        return self
