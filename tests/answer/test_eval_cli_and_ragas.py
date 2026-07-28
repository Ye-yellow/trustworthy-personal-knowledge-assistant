from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from trustworthy_kb.answer.eval_cli import main
from trustworthy_kb.answer.ragas_adapter import build_ragas_dataset, load_ragas_rows

PROJECT_ROOT = Path(__file__).parents[2]


def test_committed_golden_dataset_passes_deterministic_p0_gate(capsys) -> None:
    main(
        [
            "deterministic",
            "--cases",
            str(PROJECT_ROOT / "evals/golden/p0-cases.jsonl"),
            "--observations",
            str(PROJECT_ROOT / "evals/golden/p0-observations.jsonl"),
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is True
    assert result["metrics"]["citation_precision"] >= 0.95
    assert result["metrics"]["retrieval_recall"] >= 0.90
    assert result["metrics"]["refusal_accuracy"] == 1.0
    assert result["metrics"]["unsafe_citation_count"] == 0


def test_deterministic_gate_exits_nonzero_for_an_unsafe_citation(tmp_path, capsys) -> None:
    cases = PROJECT_ROOT / "evals/golden/p0-cases.jsonl"
    observations = [
        json.loads(line)
        for line in (PROJECT_ROOT / "evals/golden/p0-observations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    observations[0]["citation_chunk_ids"] = ["f" * 64]
    path = tmp_path / "unsafe.jsonl"
    path.write_text(
        "\n".join(json.dumps(item) for item in observations) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as captured:
        main(
            [
                "deterministic",
                "--cases",
                str(cases),
                "--observations",
                str(path),
            ]
        )

    assert captured.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is False
    assert result["metrics"]["unsafe_citation_count"] == 1


def test_optional_ragas_adapter_builds_explicit_local_dataset() -> None:
    if importlib.util.find_spec("ragas") is None:
        pytest.skip("install the eval extra to exercise RAGAS compatibility")
    rows = load_ragas_rows(PROJECT_ROOT / "evals/golden/ragas-synthetic.jsonl")

    dataset = build_ragas_dataset(rows)

    assert len(dataset) == 1
    assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"
