from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from trustworthy_kb.config import DatabaseSettings, LLMSettings
from trustworthy_kb.domain import (
    ClaimType,
    ModelRunPurpose,
    ModelRunStatus,
    Sensitivity,
)
from trustworthy_kb.governance.audit import (
    AuditedEvidenceSearchGateway,
    AuditedModelGateway,
)
from trustworthy_kb.governance.contracts import (
    ClaimDraft,
    ClaimExtractionOutput,
    ClaimObject,
    ClaimOriginSpan,
    ClaimScope,
    EvidenceSearchHit,
    EvidenceSearchRequest,
    PublicClaim,
    SearchCapabilities,
    SearchIntent,
)
from trustworthy_kb.governance.fingerprints import canonical_json_hash
from trustworthy_kb.governance.search import EvidenceSearchGateway
from trustworthy_kb.llm import ModelGateway, ModelPurpose
from trustworthy_kb.persistence import (
    Base,
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)


class FakeModelGateway:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def invoke_structured(self, _messages: object, **_kwargs: Any) -> ClaimExtractionOutput:
        if self.fail:
            raise RuntimeError("synthetic provider payload")
        return ClaimExtractionOutput(
            claims=(
                ClaimDraft(
                    claim_type=ClaimType.FACT,
                    subject="Python",
                    predicate="is",
                    object=ClaimObject(value="a language", value_type="text"),
                    sensitivity=Sensitivity.PUBLIC,
                    origins=(ClaimOriginSpan(block_anchor="body", start=0, end=6),),
                ),
            )
        )


class FakeSearchGateway:
    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities(
            supports_responses_api=True,
            supports_native_web_search=True,
            supports_url_citations=False,
            returns_provider_request_id=True,
        )

    async def search(self, _request: EvidenceSearchRequest) -> tuple[EvidenceSearchHit, ...]:
        return (
            EvidenceSearchHit(
                candidate_id="candidate-1",
                url="https://example.com/reference",
                title="Reference",
                provider_request_id="resp-synthetic",
                rank=0,
            ),
        )


def _request() -> EvidenceSearchRequest:
    claim = PublicClaim(
        claim_type=ClaimType.FACT,
        subject="Python",
        predicate="is",
        object=ClaimObject(value="a language", value_type="text"),
        scope=ClaimScope(domain="software"),
    )
    return EvidenceSearchRequest(
        claim=claim,
        intent=SearchIntent.SUPPORT,
        max_results=1,
        policy_version="search-v1",
        idempotency_hash=canonical_json_hash(claim.model_dump(mode="json")),
    )


@pytest.mark.asyncio
async def test_audit_wrappers_record_success_failure_and_search_request_id(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'audit.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    settings = LLMSettings(api_key="synthetic")
    success = AuditedModelGateway(cast(ModelGateway, FakeModelGateway()), factory, settings)
    failure = AuditedModelGateway(
        cast(ModelGateway, FakeModelGateway(fail=True)), factory, settings
    )
    search = AuditedEvidenceSearchGateway(
        cast(EvidenceSearchGateway, FakeSearchGateway()),
        success,
        model="gpt-5.5",
        prompt_version="search-v1",
    )
    try:
        output = await success.invoke_structured(
            "synthetic source",
            schema=ClaimExtractionOutput,
            purpose=ModelPurpose.CLAIM_EXTRACTION,
            metadata={"prompt_version": "extract-v1"},
        )
        with pytest.raises(RuntimeError, match="synthetic provider payload"):
            await failure.invoke_structured(
                "synthetic source",
                schema=ClaimExtractionOutput,
                purpose=ModelPurpose.CLAIM_EXTRACTION,
                metadata={"prompt_version": "extract-v1"},
            )
        await search.search(_request())

        async with factory() as unit_of_work:
            runs = tuple(await unit_of_work.audit.list_model_runs())

        assert len(output.claims) == 1
        assert [run.status for run in runs] == [
            ModelRunStatus.SUCCEEDED,
            ModelRunStatus.FAILED,
            ModelRunStatus.SUCCEEDED,
        ]
        assert runs[-1].purpose is ModelRunPurpose.EVIDENCE_SEARCH
        assert runs[-1].request_id == "resp-synthetic"
        assert all(run.input_hash != canonical_json_hash("synthetic source") for run in runs)
        assert "synthetic provider payload" not in repr(runs)
    finally:
        await engine.dispose()
