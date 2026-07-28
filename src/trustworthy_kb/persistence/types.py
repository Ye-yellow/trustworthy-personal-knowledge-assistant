"""SQLite-safe SQLAlchemy value types."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class UTCDateTime(TypeDecorator[datetime]):
    """Store timezone-aware datetimes as canonical, sortable UTC text."""

    impl = String(27)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect: Dialect | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC).strftime(_UTC_FORMAT)

    def process_result_value(
        self,
        value: str | None,
        _dialect: Dialect | None,
    ) -> datetime | None:
        if value is None:
            return None
        try:
            return datetime.strptime(value, _UTC_FORMAT).replace(tzinfo=UTC)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid UTC datetime storage") from error

    @property
    def python_type(self) -> type[datetime]:
        return datetime


class CanonicalJson(TypeDecorator[dict[str, Any]]):
    """Marker type reserved for canonical JSON mapping in table definitions."""

    from sqlalchemy import JSON as impl

    cache_ok = True


__all__ = ["CanonicalJson", "UTCDateTime"]
