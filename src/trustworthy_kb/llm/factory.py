"""LangChain chat-model construction."""

from __future__ import annotations

from typing import Any, cast

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from trustworthy_kb.config import LLMSettings

_PROVIDER_ALIASES = {"sub2api": "openai"}


def create_chat_model(settings: LLMSettings, model_name: str) -> BaseChatModel:
    """Create a LangChain model without exposing provider details to business code."""

    langchain_provider = _PROVIDER_ALIASES.get(settings.provider, settings.provider)
    kwargs: dict[str, Any] = {
        "model": model_name,
        "model_provider": langchain_provider,
        "api_key": settings.api_key.get_secret_value(),
        "timeout": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }
    if settings.base_url is not None:
        kwargs["base_url"] = settings.base_url
    if langchain_provider == "openai":
        kwargs["use_responses_api"] = False
    return cast(BaseChatModel, init_chat_model(**kwargs))
