"""Explicit async transaction boundary for all control-plane repositories."""

from __future__ import annotations

from types import TracebackType
from typing import Literal

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trustworthy_kb.persistence.audit_repository import AuditRepository
from trustworthy_kb.persistence.errors import DatabaseConfigurationError
from trustworthy_kb.persistence.knowledge_repository import KnowledgeRepository
from trustworthy_kb.persistence.publication_repository import PublicationRepository
from trustworthy_kb.persistence.repository_base import raise_operational_error
from trustworthy_kb.persistence.source_repository import SourceRepository


class SqliteUnitOfWork:
    """Own one AsyncSession and require an explicit commit."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._committed = False
        self.sources: SourceRepository
        self.knowledge: KnowledgeRepository
        self.publication: PublicationRepository
        self.audit: AuditRepository

    async def __aenter__(self) -> SqliteUnitOfWork:
        if self._session is not None:
            raise DatabaseConfigurationError("unit of work is already active")
        self._session = self._session_factory()
        self.sources = SourceRepository(self._session)
        self.knowledge = KnowledgeRepository(self._session)
        self.publication = PublicationRepository(self._session)
        self.audit = AuditRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, traceback
        session = self._require_session()
        if exc_value is not None:
            try:
                await session.rollback()
            except Exception:
                pass
            try:
                await session.close()
            except Exception:
                pass
            self._session = None
            return False

        try:
            if not self._committed:
                await session.rollback()
        finally:
            await session.close()
            self._session = None
        return False

    async def commit(self) -> None:
        """Commit all flushed repository writes exactly when requested."""

        session = self._require_session()
        if self._committed:
            raise DatabaseConfigurationError("unit of work was already committed")
        try:
            await session.commit()
        except OperationalError as error:
            raise_operational_error(error)
        self._committed = True

    async def rollback(self) -> None:
        """Explicitly roll back the active transaction."""

        await self._require_session().rollback()

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise DatabaseConfigurationError("unit of work is not active")
        return self._session


class SqliteUnitOfWorkFactory:
    """Callable factory matching ``async with unit_of_work_factory()`` usage."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> SqliteUnitOfWork:
        return SqliteUnitOfWork(self._session_factory)


__all__ = ["SqliteUnitOfWork", "SqliteUnitOfWorkFactory"]
