"""Fail-closed errors for publication and retrieval boundaries."""

from __future__ import annotations


class PublicationError(RuntimeError):
    """Base error safe to expose without document content."""


class CurationError(PublicationError):
    """The governed claims could not produce a valid curation artifact."""


class ChunkingError(PublicationError):
    """The curated artifact could not be split without violating boundaries."""


class VaultPublicationError(PublicationError):
    """A Vault path, conflict, or write verification failed."""


class IndexingError(PublicationError):
    """Index creation, upsert, or strong verification failed."""


class RetrievalError(PublicationError):
    """Retrieval could not prove its control-plane filters."""


class ReconciliationError(PublicationError):
    """The three-way reconciliation operation failed safely."""


class LifecycleError(PublicationError):
    """A delete, restore, migration, or rollback could not converge safely."""


__all__ = [
    "ChunkingError",
    "CurationError",
    "IndexingError",
    "LifecycleError",
    "PublicationError",
    "ReconciliationError",
    "RetrievalError",
    "VaultPublicationError",
]
