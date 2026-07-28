"""Loopback-only FastAPI surface for verified answers and safe progress events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from trustworthy_kb.answer import AnswerError, AnswerEvent, AnswerRequest, AnswerResult
from trustworthy_kb.api.runtime import AnswerRuntime
from trustworthy_kb.api.sse import encode_safe_stream_error, encode_sse


class AnswerServicePort(Protocol):
    async def answer(self, request: AnswerRequest) -> AnswerResult: ...

    def stream(self, request: AnswerRequest) -> AsyncIterator[AnswerEvent]: ...


class ReadinessPort(Protocol):
    async def ready(self) -> bool: ...


def create_app(
    *,
    service: AnswerServicePort | None = None,
    readiness: ReadinessPort | None = None,
) -> FastAPI:
    """Build an app without loading models until lifespan startup."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            application.state.answer_service = service
            application.state.readiness = readiness
            yield
            return
        runtime = AnswerRuntime()
        await runtime.initialize()
        application.state.answer_service = runtime.service
        application.state.readiness = runtime
        try:
            yield
        finally:
            await runtime.close()

    application = FastAPI(
        title="Trustworthy Personal Knowledge Assistant",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.exception_handler(AnswerError)
    async def answer_error_handler(_request: Request, _error: AnswerError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "trusted answer request failed"})

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "request validation failed"})

    @application.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @application.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        probe: ReadinessPort | None = request.app.state.readiness
        healthy = probe is not None and await probe.ready()
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ready" if healthy else "not_ready"},
        )

    @application.post("/v1/answers")
    async def answer(request: Request, payload: AnswerRequest) -> AnswerResult:
        answer_service: AnswerServicePort = request.app.state.answer_service
        return await answer_service.answer(payload)

    @application.post("/v1/answers/stream")
    async def stream(request: Request, payload: AnswerRequest) -> StreamingResponse:
        answer_service: AnswerServicePort = request.app.state.answer_service

        async def content() -> AsyncIterator[str]:
            try:
                async for event in answer_service.stream(payload):
                    yield encode_sse(event)
            except AnswerError:
                yield encode_safe_stream_error()

        return StreamingResponse(
            content(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    return application


app = create_app()

__all__ = ["app", "create_app"]
