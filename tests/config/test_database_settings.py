from __future__ import annotations

import pytest
from pydantic import ValidationError

from trustworthy_kb.config import DatabaseSettings


def test_database_settings_use_safe_sqlite_defaults_and_redact_url() -> None:
    settings = DatabaseSettings()

    assert settings.url_value == "sqlite+aiosqlite:///./data/trustworthy_kb.db"
    assert settings.busy_timeout_ms == 5000
    assert settings.url_value not in repr(settings)
    assert settings.url_value not in settings.model_dump_json()


def test_database_settings_load_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTKB_DATABASE_URL", "sqlite+aiosqlite:///./custom.db")
    monkeypatch.setenv("TRUSTKB_DATABASE_BUSY_TIMEOUT_MS", "7500")

    settings = DatabaseSettings()

    assert settings.url_value == "sqlite+aiosqlite:///./custom.db"
    assert settings.busy_timeout_ms == 7500


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///./sync.db",
        "postgresql+asyncpg://example.invalid/database",
        "sqlite+aiosqlite://user:secret@example.invalid/database",
    ],
)
def test_database_settings_reject_non_local_async_sqlite_urls(url: str) -> None:
    with pytest.raises(ValidationError, match=r"sqlite\+aiosqlite"):
        DatabaseSettings(url=url)
