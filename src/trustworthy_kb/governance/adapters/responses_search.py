"""OpenAI-compatible Responses web-search adapter used by sub2api and OpenAI."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from openai import AsyncOpenAI, BadRequestError, OpenAIError
from pydantic import HttpUrl, TypeAdapter, ValidationError

from trustworthy_kb.config import LLMSettings, SearchSettings
from trustworthy_kb.governance.contracts import (
    EvidenceSearchHit,
    EvidenceSearchRequest,
    SearchCapabilities,
)
from trustworthy_kb.governance.errors import (
    SearchCapabilityUnavailableError,
    SearchProviderError,
)
from trustworthy_kb.governance.search import EvidenceSearchGateway, build_public_search_prompt

_HTTP_URL = TypeAdapter(HttpUrl)
_SUPPORTED_PROVIDERS = frozenset({"sub2api", "openai"})


class ResponsesResource(Protocol):
    async def create(self, **kwargs: Any) -> Any:
        """Create a Responses API request."""


class OpenAIResponsesSearchGateway:
    """Discover candidate URLs through native Responses ``web_search``."""

    def __init__(
        self,
        *,
        responses: ResponsesResource,
        provider: str,
        model: str,
    ) -> None:
        self._responses = responses
        self._provider = provider
        self._model = model

    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities(
            supports_responses_api=True,
            supports_native_web_search=True,
            supports_url_citations=False,
            returns_provider_request_id=True,
            supported_models=(self._model,),
            limits={"candidate_source": "web_search_call.action.sources"},
        )

    async def search(self, request: EvidenceSearchRequest) -> tuple[EvidenceSearchHit, ...]:
        try:
            response = await self._responses.create(
                model=self._model,
                input=build_public_search_prompt(request),
                tools=[{"type": "web_search", "search_context_size": "low"}],
                tool_choice="required",
                include=["web_search_call.action.sources"],
            )
        except BadRequestError:
            raise SearchCapabilityUnavailableError(
                f"search capability unavailable (provider={self._provider}, model={self._model})"
            ) from None
        except OpenAIError:
            raise SearchProviderError(
                f"search provider failed (provider={self._provider}, model={self._model})"
            ) from None
        except Exception:
            raise SearchProviderError(
                f"search provider returned an invalid response "
                f"(provider={self._provider}, model={self._model})"
            ) from None
        return _parse_hits(response, max_results=request.max_results)


SearchGatewayBuilder = Callable[[SearchSettings, LLMSettings], EvidenceSearchGateway]


def create_search_gateway(
    search_settings: SearchSettings,
    llm_settings: LLMSettings,
    *,
    builders: Mapping[str, SearchGatewayBuilder] | None = None,
) -> EvidenceSearchGateway:
    """Create a search gateway while keeping provider selection at the composition root."""

    custom = (builders or {}).get(search_settings.provider)
    if custom is not None:
        return custom(search_settings, llm_settings)
    if search_settings.provider not in _SUPPORTED_PROVIDERS:
        raise SearchCapabilityUnavailableError(
            f"search provider is not registered (provider={search_settings.provider})"
        )
    if llm_settings.api_key is None:
        raise SearchProviderError(
            f"search credentials are unavailable (provider={search_settings.provider})"
        )
    model = search_settings.model or llm_settings.model
    client = AsyncOpenAI(
        api_key=llm_settings.api_key.get_secret_value(),
        base_url=llm_settings.base_url,
        timeout=search_settings.timeout_seconds,
        max_retries=llm_settings.max_retries,
    )
    return OpenAIResponsesSearchGateway(
        responses=cast(ResponsesResource, client.responses),
        provider=search_settings.provider,
        model=model,
    )


def _parse_hits(response: object, *, max_results: int) -> tuple[EvidenceSearchHit, ...]:
    payload = _response_payload(response)
    request_id = payload.get("id")
    output = payload.get("output")
    if not isinstance(request_id, str) or not request_id or not isinstance(output, list):
        raise SearchProviderError("search provider response failed contract validation")

    titles: dict[str, str] = {}
    ordered_urls: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            _collect_annotation_urls(item, ordered_urls, titles)
        if item.get("type") == "web_search_call":
            _collect_search_sources(item, ordered_urls)

    hits: list[EvidenceSearchHit] = []
    seen: set[str] = set()
    for raw_url in ordered_urls:
        try:
            url = _HTTP_URL.validate_python(raw_url)
        except ValidationError:
            continue
        normalized = str(url)
        if url.scheme != "https" or normalized in seen:
            continue
        seen.add(normalized)
        rank = len(hits)
        title = titles.get(raw_url) or urlsplit(normalized).hostname or "web source"
        candidate_hash = hashlib.sha256(f"{request_id}\n{rank}\n{normalized}".encode()).hexdigest()[
            :24
        ]
        hits.append(
            EvidenceSearchHit(
                candidate_id=f"candidate_{candidate_hash}",
                url=url,
                title=title,
                provider_request_id=request_id,
                rank=rank,
                citation_metadata={"provider_item": "web_search"},
            )
        )
        if len(hits) >= max_results:
            break
    return tuple(hits)


def _response_payload(response: object) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    model_dump = getattr(response, "model_dump", None)
    if not callable(model_dump):
        raise SearchProviderError("search provider response failed contract validation")
    dumped = model_dump(mode="json")
    if not isinstance(dumped, dict):
        raise SearchProviderError("search provider response failed contract validation")
    return dumped


def _collect_annotation_urls(
    item: dict[str, Any], ordered_urls: list[str], titles: dict[str, str]
) -> None:
    content = item.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        annotations = block.get("annotations") if isinstance(block, dict) else None
        if not isinstance(annotations, list):
            continue
        for annotation in annotations:
            if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                continue
            url = annotation.get("url")
            title = annotation.get("title")
            if isinstance(url, str):
                ordered_urls.append(url)
                if isinstance(title, str) and title.strip():
                    titles[url] = title.strip()


def _collect_search_sources(item: dict[str, Any], ordered_urls: list[str]) -> None:
    action = item.get("action")
    sources = action.get("sources") if isinstance(action, dict) else None
    if not isinstance(sources, list):
        return
    for source in sources:
        url = source.get("url") if isinstance(source, dict) else None
        if isinstance(url, str):
            ordered_urls.append(url)


__all__ = ["OpenAIResponsesSearchGateway", "create_search_gateway"]
