from __future__ import annotations

import os
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from trustworthy_kb.config import LLMSettings, SearchSettings
from trustworthy_kb.domain import ClaimType
from trustworthy_kb.governance import (
    ClaimObject,
    ClaimScope,
    EvidenceSearchRequest,
    PublicClaim,
    SearchIntent,
    canonical_json_hash,
)
from trustworthy_kb.governance.adapters import create_search_gateway
from trustworthy_kb.llm import ModelGateway, ModelPurpose, ModelRouter


class _MarkerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marker: Literal["SUB2API_STRUCTURED_OK"]


def _require_live() -> None:
    if os.environ.get("TRUSTKB_RUN_SUB2API_INTEGRATION") != "1":
        pytest.skip("set TRUSTKB_RUN_SUB2API_INTEGRATION=1 to call local sub2api")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sub2api_structured_output_and_native_web_search() -> None:
    _require_live()
    llm = LLMSettings()
    gateway = ModelGateway(ModelRouter(llm))

    structured = await gateway.invoke_structured(
        'Return JSON with marker exactly "SUB2API_STRUCTURED_OK".',
        schema=_MarkerResponse,
        purpose=ModelPurpose.CLAIM_EXTRACTION,
    )

    public_claim = PublicClaim(
        claim_type=ClaimType.FACT,
        subject="Python",
        predicate="has official documentation",
        object=ClaimObject(value="docs.python.org", value_type="text"),
        scope=ClaimScope(domain="software"),
    )
    search = create_search_gateway(SearchSettings(provider="sub2api"), llm)
    hits = await search.search(
        EvidenceSearchRequest(
            claim=public_claim,
            intent=SearchIntent.SUPPORT,
            max_results=2,
            policy_version="integration-v1",
            idempotency_hash=canonical_json_hash(public_claim.model_dump(mode="json")),
        )
    )

    assert structured.marker == "SUB2API_STRUCTURED_OK"
    assert hits
    assert all(str(hit.url).startswith("https://") for hit in hits)
    assert all(hit.provider_request_id for hit in hits)
