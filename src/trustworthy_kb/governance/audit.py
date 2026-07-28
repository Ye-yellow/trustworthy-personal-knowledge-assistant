"""Privacy-minimized audit wrappers for model and search provider boundaries."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from trustworthy_kb.config import LLMSettings
from trustworthy_kb.domain import (
    ModelRunId,
    ModelRunPurpose,
    ModelRunRecord,
    ModelRunStatus,
)
from trustworthy_kb.governance.contracts import (
    EvidenceSearchHit,
    EvidenceSearchRequest,
    SearchCapabilities,
)
from trustworthy_kb.governance.fingerprints import canonical_json_hash
from trustworthy_kb.governance.search import EvidenceSearchGateway
from trustworthy_kb.llm import ModelGateway, ModelPurpose
from trustworthy_kb.persistence import SqliteUnitOfWorkFactory
from trustworthy_kb.persistence.base import utc_now

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ModelInput = str | Sequence[BaseMessage]


class AuditedModelGateway:
    """Wrap structured LLM calls with hash-only ModelRun records."""

    def __init__(
        self,
        gateway: ModelGateway,
        unit_of_work_factory: SqliteUnitOfWorkFactory,
        settings: LLMSettings,
    ) -> None:
        self._gateway = gateway
        self._uow_factory = unit_of_work_factory
        self._settings = settings

    async def invoke_structured(
        self,
        messages: ModelInput,
        *,
        schema: type[SchemaT],
        purpose: ModelPurpose,
        metadata: Mapping[str, Any] | None = None,
        tags: Sequence[str] = (),
    ) -> SchemaT:
        prompt_version = str((metadata or {}).get("prompt_version") or "unspecified")
        input_hash = canonical_json_hash(_safe_model_input(messages))
        model_run = await self._start(
            purpose=ModelRunPurpose(purpose.value),
            prompt_version=prompt_version,
            input_hash=input_hash,
            model=_model_for(self._settings, purpose),
        )
        started = time.monotonic()
        try:
            result = await self._gateway.invoke_structured(
                messages,
                schema=schema,
                purpose=purpose,
                metadata=metadata,
                tags=tags,
            )
        except Exception as error:
            await self._finish(
                model_run,
                ModelRunStatus.FAILED,
                started,
                error_category=_error_category(error),
            )
            raise
        await self._finish(
            model_run,
            ModelRunStatus.SUCCEEDED,
            started,
            output_hash=canonical_json_hash(result.model_dump(mode="json")),
        )
        return result

    async def _start(
        self,
        *,
        purpose: ModelRunPurpose,
        prompt_version: str,
        input_hash: str,
        model: str,
    ) -> ModelRunRecord:
        async with self._uow_factory() as unit_of_work:
            record = await unit_of_work.audit.start_model_run(
                ModelRunRecord(
                    id=ModelRunId.generate(),
                    purpose=purpose,
                    provider=self._settings.provider,
                    model=model,
                    prompt_version=prompt_version,
                    status=ModelRunStatus.STARTED,
                    input_hash=input_hash,
                    revision=1,
                    started_at=utc_now(),
                )
            )
            await unit_of_work.commit()
            return record

    async def _finish(
        self,
        record: ModelRunRecord,
        status: ModelRunStatus,
        started: float,
        *,
        output_hash: str | None = None,
        request_id: str | None = None,
        error_category: str | None = None,
    ) -> None:
        async with self._uow_factory() as unit_of_work:
            await unit_of_work.audit.finish_model_run(
                record.id,
                status,
                expected_revision=record.revision,
                input_tokens=0,
                output_tokens=0,
                latency_ms=max(0, round((time.monotonic() - started) * 1000)),
                completed_at=utc_now(),
                output_hash=output_hash,
                request_id=request_id,
                error_category=error_category,
            )
            await unit_of_work.commit()


class AuditedEvidenceSearchGateway:
    """Audit search calls while preserving the provider-neutral search contract."""

    def __init__(
        self,
        gateway: EvidenceSearchGateway,
        audit_gateway: AuditedModelGateway,
        *,
        model: str,
        prompt_version: str,
    ) -> None:
        self._gateway = gateway
        self._audit = audit_gateway
        self._model = model
        self._prompt_version = prompt_version

    def capabilities(self) -> SearchCapabilities:
        return self._gateway.capabilities()

    async def search(self, request: EvidenceSearchRequest) -> tuple[EvidenceSearchHit, ...]:
        record = await self._audit._start(
            purpose=ModelRunPurpose.EVIDENCE_SEARCH,
            prompt_version=self._prompt_version,
            input_hash=canonical_json_hash(request.model_dump(mode="json")),
            model=self._model,
        )
        started = time.monotonic()
        try:
            hits = await self._gateway.search(request)
        except Exception as error:
            await self._audit._finish(
                record,
                ModelRunStatus.FAILED,
                started,
                error_category=_error_category(error),
            )
            raise
        await self._audit._finish(
            record,
            ModelRunStatus.SUCCEEDED,
            started,
            output_hash=canonical_json_hash([hit.model_dump(mode="json") for hit in hits]),
            request_id=hits[0].provider_request_id if hits else None,
        )
        return hits


def _model_for(settings: LLMSettings, purpose: ModelPurpose) -> str:
    override = {
        ModelPurpose.CLAIM_EXTRACTION: settings.extractor_model,
        ModelPurpose.EVIDENCE_VERIFICATION: settings.verifier_model,
        ModelPurpose.CURATION: settings.curation_model,
        ModelPurpose.ANSWER_GENERATION: settings.answer_model,
        ModelPurpose.EVIDENCE_SEARCH: None,
    }[purpose]
    return override or settings.model


def _safe_model_input(messages: ModelInput) -> object:
    if isinstance(messages, str):
        return {"text": messages}
    return [message.model_dump(mode="json") for message in messages]


def _error_category(error: BaseException) -> str:
    return {
        "ModelAuthenticationError": "AUTHENTICATION_FAILED",
        "ModelOutputValidationError": "OUTPUT_INVALID",
        "ModelRateLimitError": "RATE_LIMITED",
        "ModelTimeoutError": "TIMEOUT",
        "SearchCapabilityUnavailableError": "CAPABILITY_UNAVAILABLE",
        "SearchProviderError": "PROVIDER_FAILED",
    }.get(type(error).__name__, "PROVIDER_FAILED")


__all__ = ["AuditedEvidenceSearchGateway", "AuditedModelGateway"]
