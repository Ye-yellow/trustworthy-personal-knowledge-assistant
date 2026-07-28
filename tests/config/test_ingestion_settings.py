from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trustworthy_kb.config import IngestionSettings


def make_settings(tmp_path: Path, **overrides: object) -> IngestionSettings:
    vault = tmp_path / "synthetic-vault"
    vault.mkdir(exist_ok=True)
    values: dict[str, object] = {
        "vault_id": "synthetic-vault-id",
        "vault_path": str(vault),
        "snapshot_root": str(tmp_path / "snapshots"),
        "checkpoint_path": str(tmp_path / "checkpoints" / "ingestion.sqlite"),
    }
    values.update(overrides)
    return IngestionSettings(**values)  # type: ignore[arg-type]


def test_ingestion_settings_redact_private_values_and_normalize_scope(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        allowed_roots=["Projects\\Active", "."],
        excluded_roots=[".obsidian", "Archive/Private"],
    )

    assert settings.vault_id_value == "synthetic-vault-id"
    assert settings.allowed_roots == (".", "Projects/Active")
    assert settings.excluded_roots == (".obsidian", "Archive/Private")
    for private_value in (
        settings.vault_id_value,
        str(settings.vault_path_value),
        str(settings.snapshot_root_value),
        str(settings.checkpoint_path_value),
    ):
        assert private_value not in repr(settings)
        assert private_value not in settings.model_dump_json()


def test_ingestion_settings_load_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "environment-vault"
    vault.mkdir()
    monkeypatch.setenv("TRUSTKB_INGESTION_VAULT_ID", "environment-id")
    monkeypatch.setenv("TRUSTKB_INGESTION_VAULT_PATH", str(vault))
    monkeypatch.setenv("TRUSTKB_INGESTION_SNAPSHOT_ROOT", str(tmp_path / "snapshots"))
    monkeypatch.setenv("TRUSTKB_INGESTION_ALLOWED_ROOTS", '["Inbox"]')

    settings = IngestionSettings()

    assert settings.vault_id_value == "environment-id"
    assert settings.allowed_roots == ("Inbox",)


def test_ingestion_settings_reject_overlap_and_unsafe_scope_roots(tmp_path: Path) -> None:
    vault = tmp_path / "synthetic-vault"
    vault.mkdir()

    with pytest.raises(ValidationError, match="must not overlap"):
        make_settings(tmp_path, snapshot_root=str(vault / "snapshots"))
    with pytest.raises(ValidationError, match="Vault-relative"):
        make_settings(tmp_path, allowed_roots=["../outside"])
