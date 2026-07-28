"""Strict single-line JSON encoding for Server-Sent Events."""

from __future__ import annotations

import json

from trustworthy_kb.answer import AnswerEvent


def encode_sse(event: AnswerEvent) -> str:
    """Encode one trusted event without allowing field or newline injection."""

    payload = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"id: {event.event_id}\nevent: {event.event.value}\ndata: {payload}\n\n"


def encode_safe_stream_error() -> str:
    """Terminate a stream safely when execution fails before a contract event exists."""

    return 'event: error\ndata: {"detail":"trusted answer stream failed"}\n\n'


__all__ = ["encode_safe_stream_error", "encode_sse"]
