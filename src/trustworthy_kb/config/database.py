"""Validated SQLite control-plane settings."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class DatabaseSettings(BaseSettings):
    """Database settings loaded from ``TRUSTKB_DATABASE_*`` variables."""

    model_config = SettingsConfigDict(
        env_prefix="TRUSTKB_DATABASE_",
        case_sensitive=False,
        extra="ignore",
    )

    url: SecretStr = SecretStr("sqlite+aiosqlite:///./data/trustworthy_kb.db")
    busy_timeout_ms: Annotated[int, Field(ge=100, le=60_000)] = 5000

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> SecretStr:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        normalized = raw.strip()
        if not normalized:
            raise ValueError("database URL must not be empty")
        return SecretStr(normalized)

    @model_validator(mode="after")
    def _validate_local_async_sqlite(self) -> Self:
        try:
            parsed = make_url(self.url_value)
        except ArgumentError as error:
            raise ValueError("database URL must use sqlite+aiosqlite") from error
        if (
            parsed.drivername != "sqlite+aiosqlite"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.host is not None
        ):
            raise ValueError("database URL must use local sqlite+aiosqlite")
        return self

    @property
    def url_value(self) -> str:
        """Return the URL only at the database construction boundary."""

        return self.url.get_secret_value()


__all__ = ["DatabaseSettings"]
