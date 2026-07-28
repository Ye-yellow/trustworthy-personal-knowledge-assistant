"""Validated settings for claim, evidence, and quality governance."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _non_empty_secret(value: object) -> SecretStr:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    normalized = raw.strip()
    if not normalized:
        raise ValueError("value must not be empty")
    return SecretStr(normalized)


class GovernanceSettings(BaseSettings):
    """Policy and execution limits loaded from ``TRUSTKB_GOVERNANCE_*``."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTKB_GOVERNANCE_", case_sensitive=False, extra="ignore"
    )

    policy_version: str = "l3-v1"
    extractor_version: str = "claim-extractor-v1"
    verifier_version: str = "evidence-verifier-v1"
    search_policy_version: str = "search-v1"
    max_claims_per_document: Annotated[int, Field(ge=1, le=1000)] = 100
    max_concurrency: Annotated[int, Field(ge=1, le=32)] = 4
    max_retries: Annotated[int, Field(ge=0, le=10)] = 2
    evidence_snapshot_root: SecretStr = SecretStr("./storage/evidence-snapshots")
    checkpoint_path: SecretStr = SecretStr("./storage/checkpoints/governance.sqlite")

    @field_validator(
        "policy_version",
        "extractor_version",
        "verifier_version",
        "search_policy_version",
        mode="before",
    )
    @classmethod
    def _normalize_version(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("version must not be empty")
        return normalized

    @field_validator("evidence_snapshot_root", "checkpoint_path", mode="before")
    @classmethod
    def _normalize_paths(cls, value: object) -> SecretStr:
        return _non_empty_secret(value)

    @property
    def evidence_snapshot_root_value(self) -> Path:
        return Path(self.evidence_snapshot_root.get_secret_value())

    @property
    def checkpoint_path_value(self) -> Path:
        return Path(self.checkpoint_path.get_secret_value())


class SearchSettings(BaseSettings):
    """Provider-neutral search settings loaded from ``TRUSTKB_SEARCH_*``."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTKB_SEARCH_", case_sensitive=False, extra="ignore"
    )

    provider: str = "sub2api"
    model: str | None = None
    timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 120.0
    max_searches_per_claim: Annotated[int, Field(ge=1, le=10)] = 4
    max_candidates_per_claim: Annotated[int, Field(ge=1, le=50)] = 8
    live_integration: bool = False

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        if not normalized:
            raise ValueError("provider must not be empty")
        return normalized

    @field_validator("model", mode="before")
    @classmethod
    def _normalize_model(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class FetchSettings(BaseSettings):
    """Fail-closed network limits loaded from ``TRUSTKB_FETCH_*``."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTKB_FETCH_", case_sensitive=False, extra="ignore"
    )

    timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 15.0
    max_raw_bytes: Annotated[int, Field(ge=1024, le=50 * 1024 * 1024)] = 5 * 1024 * 1024
    max_decoded_bytes: Annotated[int, Field(ge=1024, le=100 * 1024 * 1024)] = 10 * 1024 * 1024
    max_redirects: Annotated[int, Field(ge=0, le=10)] = 3
    allowed_media_types: tuple[str, ...] = (
        "text/html",
        "text/plain",
        "application/pdf",
        "application/json",
    )
    user_agent: str = "TrustworthyKB-EvidenceFetcher/0.1"

    @field_validator("allowed_media_types", mode="before")
    @classmethod
    def _normalize_media_types(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("allowed media types must be a JSON array")
        normalized = tuple(
            sorted({str(item).strip().lower() for item in value if str(item).strip()})
        )
        if not normalized:
            raise ValueError("allowed media types must not be empty")
        return normalized


__all__ = ["FetchSettings", "GovernanceSettings", "SearchSettings"]
