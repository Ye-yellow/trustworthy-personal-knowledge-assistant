"""Validated settings for local Obsidian ingestion."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_EXCLUDED_ROOTS = (".obsidian", ".trash", "_AI", "attachments")


def _secret_text(value: object) -> SecretStr:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    normalized = raw.strip()
    if not normalized:
        raise ValueError("value must not be empty")
    return SecretStr(normalized)


def _normalized_scope_root(value: object) -> str:
    raw = str(value).replace("\\", "/").strip().strip("/")
    if raw in {"", "."}:
        return "."
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("scope roots must be normalized Vault-relative paths")
    return path.as_posix()


class IngestionSettings(BaseSettings):
    """Local-only settings loaded from ``TRUSTKB_INGESTION_*`` variables."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTKB_INGESTION_",
        case_sensitive=False,
        extra="ignore",
    )

    vault_id: SecretStr
    vault_path: SecretStr
    snapshot_root: SecretStr = SecretStr("./storage/source-snapshots")
    checkpoint_path: SecretStr = SecretStr("./storage/checkpoints/ingestion.sqlite")
    obsidian_executable: str = "obsidian"
    allowed_roots: tuple[str, ...] = (".",)
    excluded_roots: tuple[str, ...] = _DEFAULT_EXCLUDED_ROOTS
    max_markdown_bytes: Annotated[int, Field(ge=1024, le=50 * 1024 * 1024)] = 5 * 1024 * 1024
    stable_read_interval_ms: Annotated[int, Field(ge=50, le=5000)] = 250
    stable_read_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    cli_timeout_seconds: Annotated[float, Field(ge=1, le=300)] = 30.0
    cli_output_limit_bytes: Annotated[int, Field(ge=1024, le=64 * 1024 * 1024)] = 4 * 1024 * 1024

    @field_validator("vault_id", "vault_path", "snapshot_root", "checkpoint_path", mode="before")
    @classmethod
    def _normalize_secrets(cls, value: object) -> SecretStr:
        return _secret_text(value)

    @field_validator("obsidian_executable", mode="before")
    @classmethod
    def _normalize_executable(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("Obsidian executable must not be empty")
        return normalized

    @field_validator("allowed_roots", "excluded_roots", mode="before")
    @classmethod
    def _normalize_scope_roots(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("scope roots must be provided as a JSON array")
        roots = tuple(sorted({_normalized_scope_root(item) for item in value}))
        if not roots:
            raise ValueError("scope roots must not be empty")
        return roots

    @model_validator(mode="after")
    def _validate_local_paths(self) -> Self:
        vault = self.vault_path_value.resolve(strict=True)
        if not vault.is_dir():
            raise ValueError("Vault path must be an existing directory")
        snapshot = self.snapshot_root_value.resolve(strict=False)
        if snapshot == vault or snapshot.is_relative_to(vault) or vault.is_relative_to(snapshot):
            raise ValueError("snapshot root must not overlap the Vault path")
        return self

    @property
    def vault_id_value(self) -> str:
        """Reveal the Vault ID only at the CLI boundary."""

        return self.vault_id.get_secret_value()

    @property
    def vault_path_value(self) -> Path:
        """Reveal the Vault path only at the filesystem boundary."""

        return Path(self.vault_path.get_secret_value())

    @property
    def snapshot_root_value(self) -> Path:
        """Reveal the snapshot root only at the snapshot boundary."""

        return Path(self.snapshot_root.get_secret_value())

    @property
    def checkpoint_path_value(self) -> Path:
        """Reveal the checkpoint path only at the workflow boundary."""

        return Path(self.checkpoint_path.get_secret_value())


__all__ = ["IngestionSettings"]
