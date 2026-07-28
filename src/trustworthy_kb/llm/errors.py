"""Sanitized model errors safe for logs and API responses."""

from __future__ import annotations

import re

import openai

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class ModelGatewayError(RuntimeError):
    """Base error for the project model boundary."""


class ModelConfigurationError(ModelGatewayError):
    """Model configuration is missing or invalid."""


class ModelAuthenticationError(ModelGatewayError):
    """Provider rejected the configured credential."""


class ModelTimeoutError(ModelGatewayError):
    """Provider call exceeded its timeout."""


class ModelRateLimitError(ModelGatewayError):
    """Provider rejected the call because of a rate limit."""


class ModelProviderError(ModelGatewayError):
    """Provider call failed without a more specific safe category."""


class ModelOutputValidationError(ModelGatewayError):
    """Provider output did not satisfy the requested schema."""


def map_model_exception(
    error: Exception,
    *,
    provider: str,
    model: str,
) -> ModelGatewayError:
    """Map SDK and transport errors without retaining their sensitive messages."""

    if isinstance(error, ModelGatewayError):
        return error
    status = getattr(error, "status_code", None)
    request_id = getattr(error, "request_id", None)
    if isinstance(error, openai.AuthenticationError):
        return ModelAuthenticationError(
            _safe_error_message("authentication", provider, model, status, request_id)
        )
    if isinstance(error, openai.RateLimitError):
        return ModelRateLimitError(
            _safe_error_message("rate limit", provider, model, status, request_id)
        )
    if isinstance(error, (openai.APITimeoutError, TimeoutError)):
        return ModelTimeoutError(
            _safe_error_message("timeout", provider, model, status, request_id)
        )
    return ModelProviderError(_safe_error_message("provider", provider, model, status, request_id))


def _safe_error_message(
    category: str,
    provider: str,
    model: str,
    status: object,
    request_id: object,
) -> str:
    details = [f"provider={provider}", f"model={model}"]
    if isinstance(status, int):
        details.append(f"status={status}")
    if isinstance(request_id, str) and _SAFE_REQUEST_ID.fullmatch(request_id):
        details.append(f"request_id={request_id}")
    return f"model {category} failed ({', '.join(details)})"
