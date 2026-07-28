"""SQLite control-plane adapters for retrieval authorization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from trustworthy_kb.domain import (
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeNoteId,
)
from trustworthy_kb.persistence import SqliteUnitOfWorkFactory


class SqliteCurrentVersionResolver:
    """Resolve active note/version/generation tuples in one read transaction."""

    def __init__(self, unit_of_work_factory: SqliteUnitOfWorkFactory) -> None:
        self._uow_factory = unit_of_work_factory

    async def resolve_current_versions(
        self, note_ids: Sequence[KnowledgeNoteId]
    ) -> Mapping[KnowledgeNoteId, tuple[CuratedVersionId, IndexGenerationId]]:
        async with self._uow_factory() as unit_of_work:
            return await unit_of_work.publication.resolve_current_versions(note_ids)


__all__ = ["SqliteCurrentVersionResolver"]
