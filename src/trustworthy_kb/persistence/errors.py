"""Safe public errors for persistence boundaries."""

from __future__ import annotations


class PersistenceError(RuntimeError):
    """Base error for persistence failures safe to expose to callers."""


class DatabaseSchemaMismatchError(PersistenceError):
    """The database revision is absent or does not match the migration head."""


class DatabaseConfigurationError(PersistenceError):
    """Persistence configuration is invalid for the requested operation."""


class DatabaseBusyError(PersistenceError):
    """SQLite could not acquire the required lock before the configured timeout."""


class RecordNotFoundError(PersistenceError):
    """The requested live record does not exist."""


class DuplicateRecordError(PersistenceError):
    """A record conflicts with an existing identity or unique key."""


class ConcurrentModificationError(PersistenceError):
    """A compare-and-swap update observed a stale revision."""


class IdempotencyConflictError(PersistenceError):
    """An idempotency key was reused for a different request."""


class OperationInProgressError(PersistenceError):
    """An idempotent operation has a live lease owned by another caller."""


class IngestionAlreadyRunningError(PersistenceError):
    """A non-terminal ingestion run already exists for the requested Vault."""


__all__ = [
    "ConcurrentModificationError",
    "DatabaseBusyError",
    "DatabaseConfigurationError",
    "DatabaseSchemaMismatchError",
    "DuplicateRecordError",
    "IdempotencyConflictError",
    "IngestionAlreadyRunningError",
    "OperationInProgressError",
    "PersistenceError",
    "RecordNotFoundError",
]
