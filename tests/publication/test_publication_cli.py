from __future__ import annotations

import json
from typing import Any

import pytest

from trustworthy_kb.publication import cli
from trustworthy_kb.publication.errors import PublicationError


def test_publication_cli_emits_machine_readable_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(args: Any) -> object:
        assert args.command == "generation"
        assert args.generation_command == "create"
        return {"status": "STAGING", "generation_number": 1}

    monkeypatch.setattr(cli, "_run", fake_run)

    cli.main(["generation", "create"])

    assert json.loads(capsys.readouterr().out) == {
        "generation_number": 1,
        "status": "STAGING",
    }


def test_publication_cli_reports_only_safe_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(_args: Any) -> object:
        raise PublicationError("safe publication failure")

    monkeypatch.setattr(cli, "_run", fake_run)

    with pytest.raises(SystemExit) as captured:
        cli.main(["retrieve", "synthetic query"])

    assert captured.value.code == 1
    assert capsys.readouterr().err.strip() == "safe publication failure"
