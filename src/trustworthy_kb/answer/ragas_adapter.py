"""Explicit opt-in RAGAS 0.4 adapter using local unified LLM settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trustworthy_kb.answer.errors import AnswerIntegrityError
from trustworthy_kb.config import LLMSettings


class RagasRow(BaseModel):
    """Private local interchange row for a single-turn RAGAS evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_input: str = Field(min_length=1, max_length=4000)
    response: str = Field(min_length=1, max_length=20000)
    retrieved_contexts: tuple[str, ...] = Field(min_length=1, max_length=30)
    reference: str = Field(min_length=1, max_length=20000)


def load_ragas_rows(path: Path) -> tuple[RagasRow, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise AnswerIntegrityError("RAGAS input is unavailable") from None
    if not lines or any(not line.strip() for line in lines):
        raise AnswerIntegrityError("RAGAS input must contain non-blank JSONL records")
    try:
        return tuple(RagasRow.model_validate_json(line) for line in lines)
    except (ValidationError, ValueError):
        raise AnswerIntegrityError("RAGAS input contains an invalid record") from None


def build_ragas_dataset(rows: tuple[RagasRow, ...]) -> Any:
    """Build the optional library dataset only after explicit invocation."""

    if not rows:
        raise ValueError("RAGAS evaluation requires at least one row")
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    try:
        from ragas import EvaluationDataset, SingleTurnSample
    except ImportError:
        raise AnswerIntegrityError(
            "RAGAS is unavailable; install the project 'eval' extra"
        ) from None
    return EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=row.user_input,
                response=row.response,
                retrieved_contexts=list(row.retrieved_contexts),
                reference=row.reference,
            )
            for row in rows
        ],
        name="trustworthy-kb-local-evaluation",
    )


def evaluate_with_ragas(rows: tuple[RagasRow, ...], settings: LLMSettings) -> dict[str, float]:
    """Run basic faithfulness and context precision through the configured OpenAI boundary."""

    if settings.provider not in {"sub2api", "openai"}:
        raise AnswerIntegrityError(
            "RAGAS evaluation currently requires an OpenAI-compatible configured provider"
        )
    if settings.api_key is None:
        raise AnswerIntegrityError("RAGAS evaluation requires the configured model credential")
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    try:
        from ragas import evaluate
        from ragas.llms import llm_factory
        from ragas.metrics.collections import ContextPrecision, Faithfulness
    except ImportError:
        raise AnswerIntegrityError(
            "RAGAS is unavailable; install the project 'eval' extra"
        ) from None
    client = OpenAI(
        api_key=settings.api_key.get_secret_value(),
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )
    llm = llm_factory(
        settings.answer_model or settings.model,
        provider="openai",
        client=client,
    )
    # RAGAS 0.4's public collection metrics and LLM factory are runtime-compatible
    # with ``evaluate`` but its exported annotations still describe the legacy
    # base classes.  Keep that mismatch contained at this optional dependency edge.
    evaluate_boundary: Any = evaluate
    result: Any = evaluate_boundary(
        dataset=build_ragas_dataset(rows),
        metrics=[Faithfulness(llm), ContextPrecision(llm)],
        llm=llm,
        show_progress=False,
        raise_exceptions=False,
    )
    frame = result.to_pandas()
    metrics = {}
    for name in ("faithfulness", "context_precision"):
        if name in frame:
            metrics[name] = float(frame[name].mean())
    if set(metrics) != {"faithfulness", "context_precision"}:
        raise AnswerIntegrityError("RAGAS evaluation did not return every required metric")
    return metrics


__all__ = [
    "RagasRow",
    "build_ragas_dataset",
    "evaluate_with_ragas",
    "load_ragas_rows",
]
