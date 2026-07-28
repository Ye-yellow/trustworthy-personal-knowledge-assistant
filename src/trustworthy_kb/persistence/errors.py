"""Safe public errors for persistence boundaries."""

from __future__ import annotations


class PersistenceError(RuntimeError):
    """Base error for persistence failures safe to expose to callers."""


class DatabaseSchemaMismatchError(PersistenceError):
    """The database revision is absent or does not match the migration head."""


__all__ = ["DatabaseSchemaMismatchError", "PersistenceError"]
