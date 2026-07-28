"""Validated local-only settings for trusted answers and the API."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AnswerSettings(BaseSettings):
    """Question-answering policy and loopback API settings."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTKB_ANSWER_",
        case_sensitive=False,
        extra="ignore",
    )

    prompt_version: str = "answer-v1"
    citation_verifier_version: str = "answer-citation-v1"
    max_question_characters: Annotated[int, Field(ge=1, le=4000)] = 4000
    max_answer_claims: Annotated[int, Field(ge=1, le=12)] = 12
    max_claim_characters: Annotated[int, Field(ge=1, le=1000)] = 1000
    min_evidence_count: Annotated[int, Field(ge=1, le=10)] = 1
    default_top_k: Annotated[int, Field(ge=1, le=10)] = 5
    snapshot_root: SecretStr = SecretStr("./storage/answer-snapshots")
    api_host: str = "127.0.0.1"
    api_port: Annotated[int, Field(ge=1024, le=65535)] = 8765

    @field_validator("prompt_version", "citation_verifier_version", mode="before")
    @classmethod
    def _version(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("answer policy versions must not be empty")
        return normalized

    @field_validator("api_host", mode="before")
    @classmethod
    def _loopback_host(cls, value: object) -> str:
        normalized = str(value).strip()
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            raise ValueError("answer API host must be a loopback IP address") from None
        if not address.is_loopback:
            raise ValueError("answer API host must be a loopback IP address")
        return str(address)

    @field_validator("snapshot_root", mode="before")
    @classmethod
    def _private_snapshot_root(cls, value: object) -> SecretStr:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        normalized = raw.strip()
        if not normalized:
            raise ValueError("answer snapshot root must not be empty")
        return SecretStr(normalized)

    @model_validator(mode="after")
    def _evidence_budget(self) -> Self:
        if self.min_evidence_count > self.default_top_k:
            raise ValueError("minimum evidence count cannot exceed default top-k")
        return self

    @property
    def snapshot_root_value(self) -> Path:
        return Path(self.snapshot_root.get_secret_value()).expanduser().resolve(strict=False)


__all__ = ["AnswerSettings"]
