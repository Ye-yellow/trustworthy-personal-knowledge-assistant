from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from trustworthy_kb.answer import (
    AnswerCitation,
    AnsweredResult,
    AnswerEvent,
    AnswerEventType,
    AnswerRequest,
    AnswerResult,
    DraftAnswerClaim,
)
from trustworthy_kb.api import create_app
from trustworthy_kb.domain import (
    AnswerRunId,
    ClaimStatus,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeNoteId,
    SourceVersionId,
)


class Ready:
    async def ready(self) -> bool:
        return True


class Service:
    def __init__(self, result: AnsweredResult) -> None:
        self.result = result

    async def answer(self, _request: AnswerRequest) -> AnswerResult:
        return self.result

    async def stream(self, _request: AnswerRequest):
        yield AnswerEvent(
            event_id=1,
            event=AnswerEventType.ACCEPTED,
            run_id=self.result.run_id,
            occurred_at=datetime.now(UTC),
            payload={"status": "accepted"},
        )
        yield AnswerEvent(
            event_id=2,
            event=AnswerEventType.ANSWER,
            run_id=self.result.run_id,
            occurred_at=datetime.now(UTC),
            payload={"status": "ANSWERED"},
            result=self.result,
        )


def _result() -> AnsweredResult:
    chunk_id = "a" * 64
    return AnsweredResult(
        run_id=AnswerRunId.generate(),
        answer_markdown="Synthetic verified answer.[1]\n",
        claims=(
            DraftAnswerClaim(
                statement="Synthetic verified answer.",
                citation_chunk_ids=(chunk_id,),
            ),
        ),
        citations=(
            AnswerCitation(
                number=1,
                chunk_id=chunk_id,
                note_id=KnowledgeNoteId.generate(),
                curated_version_id=CuratedVersionId.generate(),
                source_version_ids=(SourceVersionId.generate(),),
                quality_status=ClaimStatus.VERIFIED,
                vault_path="40-Concepts/Synthetic.md",
                heading_path=("Synthetic",),
                wikilink="[[40-Concepts/Synthetic#Synthetic]]",
            ),
        ),
        generation_id=IndexGenerationId.generate(),
    )


async def test_answer_and_health_endpoints_return_strict_local_contracts() -> None:
    result = _result()
    app = create_app(service=Service(result), readiness=Ready())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            live = await client.get("/health/live")
            ready = await client.get("/health/ready")
            response = await client.post("/v1/answers", json={"question": "Synthetic?"})

    assert live.json() == {"status": "live"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert response.status_code == 200
    assert response.json()["status"] == "ANSWERED"
    assert response.json()["run_id"] == str(result.run_id)
    assert "access-control-allow-origin" not in response.headers


async def test_sse_endpoint_streams_progress_then_verified_terminal_result() -> None:
    result = _result()
    app = create_app(service=Service(result), readiness=Ready())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/answers/stream", json={"question": "Synthetic?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert "event: accepted" in response.text
    assert "event: answer" in response.text
    assert response.text.index("event: accepted") < response.text.index("event: answer")
    accepted = response.text.split("event: answer", maxsplit=1)[0]
    assert "Synthetic verified answer" not in accepted


async def test_api_rejects_invalid_question_before_service_execution() -> None:
    app = create_app(service=Service(_result()), readiness=Ready())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/answers", json={"question": "unsafe\u0000question"})

    assert response.status_code == 422
    assert "unsafe" not in response.text
