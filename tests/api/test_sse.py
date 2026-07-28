from datetime import UTC, datetime

from trustworthy_kb.answer import AnswerEvent, AnswerEventType
from trustworthy_kb.api.sse import encode_sse
from trustworthy_kb.domain import AnswerRunId


def test_sse_encoder_uses_closed_event_name_and_single_line_json() -> None:
    event = AnswerEvent(
        event_id=7,
        event=AnswerEventType.RETRIEVED,
        run_id=AnswerRunId.generate(),
        occurred_at=datetime.now(UTC),
        payload={"status": "line one\nline two"},
    )

    encoded = encode_sse(event)

    assert encoded.startswith("id: 7\nevent: retrieved\ndata: {")
    assert "line one\\nline two" in encoded
    assert encoded.endswith("\n\n")
    assert len(encoded.splitlines()) == 4
    assert encoded.splitlines()[2].startswith("data: ")
