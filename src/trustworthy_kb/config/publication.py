"""Validated local Vault publication and retrieval settings."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Self

from pydantic import AnyHttpUrl, Field, SecretStr, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HTTP_URL = TypeAdapter(AnyHttpUrl)
_COLLECTION_PREFIX = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class PublicationSettings(BaseSettings):
    """Local-only filesystem and curation settings."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTKB_PUBLICATION_",
        case_sensitive=False,
        extra="ignore",
    )

    vault_path: SecretStr
    snapshot_root: SecretStr = SecretStr("./storage/publication-snapshots")
    staging_root: str = "_AI/Staging"
    versions_root: str = "_AI/Versions"
    trash_root: str = "_AI/Trash"
    note_root: str = "40-Concepts"
    prompt_version: str = "curation-v1"
    chunker_version: str = "markdown-v1"
    schema_version: str = "milvus-hybrid-v1"
    max_markdown_bytes: Annotated[int, Field(ge=1024, le=50 * 1024 * 1024)] = 5 * 1024 * 1024

    @field_validator("vault_path", "snapshot_root", mode="before")
    @classmethod
    def _private_path(cls, value: object) -> SecretStr:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        normalized = raw.strip()
        if not normalized:
            raise ValueError("publication paths must not be empty")
        return SecretStr(normalized)

    @field_validator("staging_root", "versions_root", "trash_root", "note_root", mode="before")
    @classmethod
    def _relative_root(cls, value: object) -> str:
        text = str(value).strip()
        if "\\" in text:
            raise ValueError("publication roots must use normalized Vault-relative paths")
        path = PurePosixPath(text)
        if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("publication roots must use normalized Vault-relative paths")
        return path.as_posix()

    @field_validator("prompt_version", "chunker_version", "schema_version", mode="before")
    @classmethod
    def _version_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("publication versions must not be empty")
        return normalized

    @model_validator(mode="after")
    def _local_boundaries(self) -> Self:
        vault_input = self.vault_path_value.expanduser()
        if vault_input.is_symlink():
            raise ValueError("publication Vault path must be a real directory")
        vault = vault_input.resolve(strict=True)
        if not vault.is_dir():
            raise ValueError("publication Vault path must be a real directory")
        snapshot_input = self.snapshot_root_value.expanduser()
        if snapshot_input.exists() and snapshot_input.is_symlink():
            raise ValueError("publication snapshot root must not be a symlink")
        snapshot = snapshot_input.resolve(strict=False)
        if snapshot == vault or snapshot.is_relative_to(vault) or vault.is_relative_to(snapshot):
            raise ValueError("publication snapshot root must not overlap the Vault")
        roots = (self.staging_root, self.versions_root, self.trash_root, self.note_root)
        if len(set(roots)) != len(roots):
            raise ValueError("publication Vault roots must be distinct")
        return self

    @property
    def vault_path_value(self) -> Path:
        return Path(self.vault_path.get_secret_value())

    @property
    def snapshot_root_value(self) -> Path:
        return Path(self.snapshot_root.get_secret_value())


class RetrievalSettings(BaseSettings):
    """Provider-neutral vector, embedding, and reranking configuration."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTKB_RETRIEVAL_",
        case_sensitive=False,
        extra="ignore",
    )

    vector_provider: str = "milvus"
    milvus_uri: str = "http://localhost:19530"
    milvus_token: SecretStr | None = None
    collection_prefix: str = "trustworthy_kb_chunks_g"
    consistency: str = "Bounded"
    timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 30.0
    embedding_provider: str = "bge"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: Annotated[int, Field(ge=2, le=65536)] = 1024
    embedding_device: str | None = None
    embedding_batch_size: Annotated[int, Field(ge=1, le=512)] = 16
    reranker_provider: str = "bge"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_device: str | None = None
    reranker_batch_size: Annotated[int, Field(ge=1, le=512)] = 8
    model_cache_root: str = "./storage/model-cache"
    allow_bm25_only: bool = False
    rrf_k: Annotated[int, Field(ge=1, le=16383)] = 60

    @field_validator(
        "vector_provider",
        "embedding_provider",
        "reranker_provider",
        mode="before",
    )
    @classmethod
    def _provider(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        if not normalized:
            raise ValueError("retrieval providers must not be empty")
        return normalized

    @field_validator("embedding_model", "reranker_model", mode="before")
    @classmethod
    def _model(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("retrieval models must not be empty")
        return normalized

    @field_validator("embedding_device", "reranker_device", mode="before")
    @classmethod
    def _device(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("model_cache_root", mode="before")
    @classmethod
    def _cache_root(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("retrieval model cache root must not be empty")
        return normalized

    @field_validator("milvus_token", mode="before")
    @classmethod
    def _token(cls, value: object) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        normalized = raw.strip()
        return SecretStr(normalized) if normalized else None

    @model_validator(mode="after")
    def _supported_configuration(self) -> Self:
        if self.vector_provider != "milvus":
            raise ValueError("unsupported vector provider")
        if self.embedding_provider != "bge" or self.reranker_provider not in {"bge", "none"}:
            raise ValueError("unsupported local retrieval model provider")
        self.milvus_uri = str(
            _HTTP_URL.validate_python(self.milvus_uri.strip().rstrip("/"))
        ).rstrip("/")
        if not _COLLECTION_PREFIX.fullmatch(self.collection_prefix):
            raise ValueError("Milvus collection prefix is invalid")
        canonical = self.consistency.strip().title()
        if canonical not in {"Strong", "Bounded", "Session", "Eventually"}:
            raise ValueError("Milvus consistency level is invalid")
        self.consistency = canonical
        return self

    @property
    def milvus_token_value(self) -> str | None:
        return None if self.milvus_token is None else self.milvus_token.get_secret_value()

    @property
    def model_cache_root_value(self) -> Path:
        return Path(self.model_cache_root).expanduser().resolve(strict=False)


__all__ = ["PublicationSettings", "RetrievalSettings"]
