"""Shared, safe mechanics for async repositories."""

from __future__ import annotations

import sqlite3
from typing import NoReturn

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from trustworthy_kb.domain.errors import InvariantViolationError
from trustworthy_kb.domain.ids import TypedId
from trustworthy_kb.persistence.errors import (
    ConcurrentModificationError,
    DatabaseBusyError,
    DuplicateRecordError,
    RecordNotFoundError,
)


def to_record[RecordT: BaseModel](record_type: type[RecordT], row: object) -> RecordT:
    """Convert an internal ORM row into a frozen public domain record."""

    return record_type.model_validate(row, from_attributes=True)


def redact_identifier(identifier: TypedId | str) -> str:
    """Render an identifier without exposing its complete value."""

    value = str(identifier)
    prefix, separator, _suffix = value.partition("_")
    if not separator:
        return "…"
    return f"{prefix}_…{value[-4:]}"


def not_found(entity: str, identifier: TypedId | str) -> RecordNotFoundError:
    return RecordNotFoundError(f"{entity} not found ({redact_identifier(identifier)})")


def concurrent(entity: str, identifier: TypedId | str) -> ConcurrentModificationError:
    return ConcurrentModificationError(
        f"{entity} revision is stale ({redact_identifier(identifier)})"
    )


def invariant(entity: str, identifier: TypedId | str) -> InvariantViolationError:
    return InvariantViolationError(f"{entity} invariant failed ({redact_identifier(identifier)})")


async def flush_safely(
    session: AsyncSession,
    *,
    entity: str,
    identifier: TypedId | str,
) -> None:
    """Flush pending writes and translate driver errors into safe public errors."""

    try:
        await session.flush()
    except IntegrityError as error:
        raise_constraint_error(error, entity=entity, identifier=identifier)
    except OperationalError as error:
        raise_operational_error(error)


def raise_constraint_error(
    error: IntegrityError,
    *,
    entity: str,
    identifier: TypedId | str,
) -> NoReturn:
    error_name = getattr(error.orig, "sqlite_errorname", "")
    if error_name in {"SQLITE_CONSTRAINT_PRIMARYKEY", "SQLITE_CONSTRAINT_UNIQUE"}:
        raise DuplicateRecordError(
            f"duplicate {entity} ({redact_identifier(identifier)})"
        ) from None
    raise invariant(entity, identifier) from None


def raise_operational_error(error: OperationalError) -> NoReturn:
    error_code = getattr(error.orig, "sqlite_errorcode", None)
    if error_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        raise DatabaseBusyError("database is busy; retry through an idempotent operation") from None
    raise error


__all__ = [
    "concurrent",
    "flush_safely",
    "invariant",
    "not_found",
    "raise_constraint_error",
    "raise_operational_error",
    "redact_identifier",
    "to_record",
]
