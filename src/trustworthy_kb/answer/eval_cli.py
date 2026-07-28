"""Command line quality gates for deterministic Golden and optional RAGAS evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from trustworthy_kb.answer.contracts import GoldenObservation
from trustworthy_kb.answer.errors import AnswerError
from trustworthy_kb.answer.evaluation import evaluate_observations, load_golden_cases
from trustworthy_kb.answer.ragas_adapter import evaluate_with_ragas, load_ragas_rows
from trustworthy_kb.config import LLMSettings

_DEFAULT_CASES = Path("evals/golden/p0-cases.jsonl")
_DEFAULT_OBSERVATIONS = Path("evals/golden/p0-observations.jsonl")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trustworthy-kb-eval")
    commands = parser.add_subparsers(dest="command", required=True)
    deterministic = commands.add_parser("deterministic")
    deterministic.add_argument("--cases", type=Path, default=_DEFAULT_CASES)
    deterministic.add_argument("--observations", type=Path, default=_DEFAULT_OBSERVATIONS)
    deterministic.add_argument("--generation-id")
    ragas = commands.add_parser("ragas")
    ragas.add_argument("rows", type=Path)
    return parser


def _observations(path: Path) -> tuple[GoldenObservation, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or any(not line.strip() for line in lines):
            raise ValueError
        return tuple(GoldenObservation.model_validate_json(line) for line in lines)
    except (OSError, ValidationError, ValueError):
        raise AnswerError("Golden observations are unavailable or invalid") from None


def _run(args: argparse.Namespace) -> tuple[dict[str, object], bool]:
    if args.command == "ragas":
        ragas_metrics = evaluate_with_ragas(
            load_ragas_rows(args.rows), LLMSettings(_env_file=".env")
        )
        return {"mode": "ragas", "metrics": ragas_metrics}, True
    evaluation_metrics = evaluate_observations(
        load_golden_cases(args.cases),
        _observations(args.observations),
    )
    passed = (
        evaluation_metrics.citation_precision >= 0.95
        and evaluation_metrics.retrieval_recall >= 0.90
        and evaluation_metrics.refusal_accuracy == 1.0
        and evaluation_metrics.unsafe_citation_count == 0
    )
    result: dict[str, object] = {
        "mode": "deterministic",
        "passed": passed,
        "metrics": evaluation_metrics.model_dump(mode="json"),
    }
    if args.generation_id:
        result["generation_id"] = args.generation_id
    return result, passed


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        result, passed = _run(args)
    except Exception as error:
        message = str(error) if isinstance(error, AnswerError) else "evaluation failed safely"
        print(message, file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if not passed:
        raise SystemExit(1)


__all__ = ["main"]
