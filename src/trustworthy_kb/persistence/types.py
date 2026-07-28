"""SQLite-safe SQLAlchemy value types."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import String
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from trustworthy_kb.domain.ids import TypedId, parse_typed_id

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
IdT = TypeVar("IdT", bound=TypedId)


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


class TypedIdType(TypeDecorator[IdT]):
    """Persist a concrete typed ID as text and restore its runtime type."""

    impl = String
    cache_ok = True

    def __init__(self, id_type: type[IdT]) -> None:
        self.id_type = id_type
        super().__init__(length=len(id_type.prefix) + 26)

    def process_bind_param(self, value: IdT | str | None, _dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(self.id_type(str(value)))

    def process_result_value(self, value: str | None, _dialect: Dialect) -> IdT | None:
        return self.id_type(value) if value is not None else None

    @property
    def python_type(self) -> type[IdT]:
        return self.id_type


class AnyTypedIdType(TypeDecorator[TypedId]):
    """Persist and restore any registered typed ID for polymorphic references."""

    impl = String(64)
    cache_ok = True

    def process_bind_param(
        self,
        value: TypedId | str | None,
        _dialect: Dialect,
    ) -> str | None:
        return str(parse_typed_id(str(value))) if value is not None else None

    def process_result_value(self, value: str | None, _dialect: Dialect) -> TypedId | None:
        return parse_typed_id(value) if value is not None else None

    @property
    def python_type(self) -> type[TypedId]:
        return TypedId


__all__ = ["AnyTypedIdType", "CanonicalJson", "TypedIdType", "UTCDateTime"]
