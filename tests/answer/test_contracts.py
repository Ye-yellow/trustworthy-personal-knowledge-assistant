from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trustworthy_kb.answer import AnswerRequest, QueryPlan, QueryScope
from trustworthy_kb.answer.contracts import PlannedScope


def test_answer_request_normalizes_safe_bounded_input() -> None:
    request = AnswerRequest(
        question="  What is supported?  ",
        scope=QueryScope.GENERAL,
        as_of=datetime.now(UTC),
        software_version="  3.12  ",
        operation_id="  synthetic-operation  ",
    )

    assert request.question == "What is supported?"
    assert request.software_version == "3.12"
    assert request.operation_id == "synthetic-operation"


@pytest.mark.parametrize("question", ["", " \n ", "unsafe\x00question", "unsafe\x07question"])
def test_answer_request_rejects_empty_or_control_character_input(question: str) -> None:
    with pytest.raises(ValidationError):
        AnswerRequest(question=question)


def test_query_plan_rejects_unknown_fields_and_empty_query() -> None:
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(
            {
                "normalized_query": " ",
                "scope": PlannedScope.GENERAL,
                "allow_quarantined": True,
            }
        )
