"""Trusted question-answering contracts and deterministic safety gates."""

from trustworthy_kb.answer.citation_verifier import AnswerCitationVerifier
from trustworthy_kb.answer.contracts import (
    AnswerCitation,
    AnswerDraft,
    AnsweredResult,
    AnswerEvent,
    AnswerEventType,
    AnswerEvidence,
    AnswerRequest,
    AnswerResult,
    AnswerStatus,
    CitationSupportDecision,
    CitationVerificationOutput,
    DraftAnswerClaim,
    EvaluationMetrics,
    GoldenCase,
    GoldenObservation,
    PlannedScope,
    QueryPlan,
    QueryScope,
    RefusalCode,
    RefusedResult,
)
from trustworthy_kb.answer.errors import AnswerError, AnswerIntegrityError, AnswerUnavailableError
from trustworthy_kb.answer.evaluation import (
    evaluate_observations,
    export_ragas_jsonl,
    load_golden_cases,
)
from trustworthy_kb.answer.generation import StructuredAnswerGenerator
from trustworthy_kb.answer.planning import AnswerPlanner, retrieval_query_for_plan
from trustworthy_kb.answer.rendering import render_verified_answer
from trustworthy_kb.answer.verification import (
    validate_citation_closed_set,
    validate_semantic_support,
)

__all__ = [
    "AnswerCitation",
    "AnswerCitationVerifier",
    "AnswerDraft",
    "AnswerError",
    "AnswerEvent",
    "AnswerEventType",
    "AnswerEvidence",
    "AnswerIntegrityError",
    "AnswerPlanner",
    "AnswerRequest",
    "AnswerResult",
    "AnswerStatus",
    "AnswerUnavailableError",
    "AnsweredResult",
    "CitationSupportDecision",
    "CitationVerificationOutput",
    "DraftAnswerClaim",
    "EvaluationMetrics",
    "GoldenCase",
    "GoldenObservation",
    "PlannedScope",
    "QueryPlan",
    "QueryScope",
    "RefusalCode",
    "RefusedResult",
    "StructuredAnswerGenerator",
    "evaluate_observations",
    "export_ragas_jsonl",
    "load_golden_cases",
    "render_verified_answer",
    "retrieval_query_for_plan",
    "validate_citation_closed_set",
    "validate_semantic_support",
]
