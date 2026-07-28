import json

import pytest

from trustworthy_kb.answer import (
    AnswerIntegrityError,
    GoldenCase,
    GoldenObservation,
    RefusalCode,
    evaluate_observations,
    load_golden_cases,
)


def test_golden_metrics_cover_citations_retrieval_refusal_and_unsafe_counts() -> None:
    allowed = "a" * 64
    forbidden = "b" * 64
    cases = (
        GoldenCase(
            case_id="answer",
            question="Synthetic answer?",
            should_refuse=False,
            expected_chunk_ids=(allowed,),
            allowed_citation_chunk_ids=(allowed,),
            forbidden_citation_chunk_ids=(forbidden,),
        ),
        GoldenCase(
            case_id="refusal",
            question="Unknown?",
            should_refuse=True,
            expected_refusal_code=RefusalCode.NO_TRUSTED_EVIDENCE,
        ),
    )
    observations = (
        GoldenObservation(
            case_id="answer",
            refused=False,
            retrieved_chunk_ids=(allowed,),
            citation_chunk_ids=(allowed,),
        ),
        GoldenObservation(
            case_id="refusal",
            refused=True,
            refusal_code=RefusalCode.NO_TRUSTED_EVIDENCE,
        ),
    )

    metrics = evaluate_observations(cases, observations)

    assert metrics.citation_precision == 1.0
    assert metrics.retrieval_recall == 1.0
    assert metrics.refusal_accuracy == 1.0
    assert metrics.unsafe_citation_count == 0


def test_golden_loader_rejects_duplicate_or_blank_records(tmp_path) -> None:
    record = GoldenCase(
        case_id="duplicate",
        question="Synthetic?",
        should_refuse=True,
        expected_refusal_code=RefusalCode.NO_TRUSTED_EVIDENCE,
    ).model_dump(mode="json")
    path = tmp_path / "golden.jsonl"
    path.write_text(
        json.dumps(record) + "\n" + json.dumps(record) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AnswerIntegrityError, match="unique"):
        load_golden_cases(path)


def test_golden_observations_must_cover_cases_exactly_once() -> None:
    case = GoldenCase(
        case_id="only",
        question="Synthetic?",
        should_refuse=True,
        expected_refusal_code=RefusalCode.NO_TRUSTED_EVIDENCE,
    )

    with pytest.raises(AnswerIntegrityError, match="exactly once"):
        evaluate_observations((case,), ())
