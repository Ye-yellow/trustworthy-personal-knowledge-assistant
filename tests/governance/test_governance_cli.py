from __future__ import annotations

import json
from typing import Any

import pytest

from trustworthy_kb.governance import cli
from trustworthy_kb.governance.errors import GovernanceError


def test_governance_cli_emits_machine_readable_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_run(args: Any) -> object:
        assert args.command == "run"
        return [{"status": "COMPLETED", "total": 1}]

    monkeypatch.setattr(cli, "_run", fake_run)

    cli.main(["run"])

    assert json.loads(capsys.readouterr().out) == [{"status": "COMPLETED", "total": 1}]


def test_governance_cli_redacts_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_run(_args: Any) -> object:
        raise GovernanceError("safe governance failure")

    monkeypatch.setattr(cli, "_run", fake_run)

    with pytest.raises(SystemExit) as captured:
        cli.main(["review", "list"])

    assert captured.value.code == 1
    assert capsys.readouterr().err.strip() == "safe governance failure"
