from __future__ import annotations

import httpx
import openai

from trustworthy_kb.llm import (
    ModelAuthenticationError,
    ModelProviderError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from trustworthy_kb.llm.errors import map_model_exception


def _response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
    return httpx.Response(status_code, request=request)


def test_authentication_error_is_mapped_without_original_body() -> None:
    error = openai.AuthenticationError(
        "bad secret-value",
        response=_response(401),
        body={"error": "secret-value"},
    )

    mapped = map_model_exception(error, provider="sub2api", model="gpt-5.5")

    assert isinstance(mapped, ModelAuthenticationError)
    assert "secret-value" not in str(mapped)
    assert "status=401" in str(mapped)


def test_rate_limit_and_timeout_errors_are_stable() -> None:
    request = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
    rate_limit = openai.RateLimitError("limited", response=_response(429), body=None)
    timeout = openai.APITimeoutError(request=request)

    assert isinstance(
        map_model_exception(rate_limit, provider="sub2api", model="gpt-5.5"),
        ModelRateLimitError,
    )
    assert isinstance(
        map_model_exception(timeout, provider="sub2api", model="gpt-5.5"),
        ModelTimeoutError,
    )


def test_unknown_error_is_mapped_to_provider_error() -> None:
    mapped = map_model_exception(
        RuntimeError("contains secret-value"),
        provider="sub2api",
        model="gpt-5.5",
    )

    assert isinstance(mapped, ModelProviderError)
    assert "secret-value" not in str(mapped)
