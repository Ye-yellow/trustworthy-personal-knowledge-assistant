from __future__ import annotations

from typing import Any

import httpx
import pytest
from openai import BadRequestError

from trustworthy_kb.config import LLMSettings, SearchSettings
from trustworthy_kb.domain import ClaimType
from trustworthy_kb.governance import (
    ClaimObject,
    ClaimScope,
    EvidenceSearchRequest,
    PublicClaim,
    SearchIntent,
    build_public_search_prompt,
)
from trustworthy_kb.governance.adapters.responses_search import (
    OpenAIResponsesSearchGateway,
    create_search_gateway,
)
from trustworthy_kb.governance.errors import (
    SearchCapabilityUnavailableError,
    SearchProviderError,
)


def search_request(*, max_results: int = 8) -> EvidenceSearchRequest:
    return EvidenceSearchRequest(
        claim=PublicClaim(
            claim_type=ClaimType.FACT,
            subject="Python",
            predicate="documents",
            object=ClaimObject(value="language", value_type="text"),
            scope=ClaimScope(domain="software", version="3.13"),
        ),
        intent=SearchIntent.SUPPORT,
        version_constraints=("3.13",),
        max_results=max_results,
        policy_version="search-v1",
        idempotency_hash="a" * 64,
    )


class FakeResponses:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_responses_search_parses_sources_and_citations_as_untrusted_candidates() -> None:
    response = FakeResponses(
        {
            "id": "resp_synthetic",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "synthetic",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://docs.python.org/3/",
                                    "title": "Python documentation",
                                }
                            ],
                        }
                    ],
                },
                {
                    "type": "web_search_call",
                    "action": {
                        "type": "search",
                        "sources": [
                            {"type": "url", "url": "https://docs.python.org/3/"},
                            {"type": "url", "url": "https://www.python.org/doc/"},
                            {"type": "url", "url": "http://unsafe.example/test"},
                        ],
                    },
                },
            ],
        }
    )
    gateway = OpenAIResponsesSearchGateway(responses=response, provider="sub2api", model="gpt-5.5")

    hits = await gateway.search(search_request(max_results=2))

    assert [str(hit.url) for hit in hits] == [
        "https://docs.python.org/3/",
        "https://www.python.org/doc/",
    ]
    assert hits[0].title == "Python documentation"
    assert all(hit.provider_request_id == "resp_synthetic" for hit in hits)
    assert response.calls[0]["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert response.calls[0]["include"] == ["web_search_call.action.sources"]
    assert "unsafe.example" not in repr(hits)


def test_public_search_prompt_is_deterministic_and_contains_only_structured_request() -> None:
    request = search_request()

    first = build_public_search_prompt(request)
    second = build_public_search_prompt(request)

    assert first == second
    assert "Python" in first
    assert "3.13" in first
    assert "idempotency_hash" not in first
    assert "policy_version" not in first


@pytest.mark.asyncio
async def test_responses_search_maps_capability_and_transport_failures_safely() -> None:
    http_request = httpx.Request("POST", "https://example.invalid/v1/responses")
    bad_request = BadRequestError(
        "contains-provider-payload",
        response=httpx.Response(400, request=http_request),
        body=None,
    )
    capability_gateway = OpenAIResponsesSearchGateway(
        responses=FakeResponses(error=bad_request), provider="sub2api", model="gpt-5.5"
    )
    transport_gateway = OpenAIResponsesSearchGateway(
        responses=FakeResponses(error=RuntimeError("contains-secret")),
        provider="sub2api",
        model="gpt-5.5",
    )

    with pytest.raises(SearchCapabilityUnavailableError) as capability:
        await capability_gateway.search(search_request())
    with pytest.raises(SearchProviderError) as transport:
        await transport_gateway.search(search_request())

    assert "contains-provider-payload" not in str(capability.value)
    assert "contains-secret" not in str(transport.value)


@pytest.mark.asyncio
async def test_responses_search_rejects_response_without_request_id() -> None:
    gateway = OpenAIResponsesSearchGateway(
        responses=FakeResponses({"output": []}), provider="sub2api", model="gpt-5.5"
    )

    with pytest.raises(SearchProviderError, match="contract validation"):
        await gateway.search(search_request())


def test_search_gateway_factory_supports_registration_and_rejects_unknown_provider() -> None:
    sentinel = object()

    def builder(_search: SearchSettings, _llm: LLMSettings) -> Any:
        return sentinel

    custom = create_search_gateway(
        SearchSettings(provider="custom"),
        LLMSettings(api_key="synthetic"),
        builders={"custom": builder},
    )
    assert custom is sentinel

    with pytest.raises(SearchCapabilityUnavailableError, match="not registered"):
        create_search_gateway(SearchSettings(provider="unknown"), LLMSettings(api_key="synthetic"))


def test_search_gateway_reports_missing_credentials_without_exposing_values() -> None:
    with pytest.raises(SearchProviderError, match="credentials are unavailable"):
        create_search_gateway(
            SearchSettings(provider="sub2api"),
            LLMSettings(provider="ollama", model="synthetic"),
        )
