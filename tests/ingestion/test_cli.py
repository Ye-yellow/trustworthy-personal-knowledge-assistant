from __future__ import annotations

import json
from typing import Any

import pytest

from trustworthy_kb.domain import IngestionRunId, IngestionRunStatus
from trustworthy_kb.ingestion import cli
from trustworthy_kb.ingestion.runner import IngestionReport


def test_main_prints_checkpoint_safe_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = IngestionReport(
        run_id=IngestionRunId.generate(),
        status=IngestionRunStatus.COMPLETED,
        total=3,
        succeeded=2,
        skipped=1,
        quarantined=0,
        failed=0,
    )

    async def fake_run() -> IngestionReport:
        return report

    monkeypatch.setattr(cli, "_run_configured_ingestion", fake_run)

    cli.main()

    captured = capsys.readouterr()
    output: dict[str, Any] = json.loads(captured.out)
    assert output == json.loads(report.model_dump_json())
    assert captured.err == ""


def test_main_hides_unexpected_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def failing_run() -> IngestionReport:
        raise RuntimeError("private vault path and note body")

    monkeypatch.setattr(cli, "_run_configured_ingestion", failing_run)

    with pytest.raises(SystemExit, match="1"):
        cli.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ingestion failed\n"
