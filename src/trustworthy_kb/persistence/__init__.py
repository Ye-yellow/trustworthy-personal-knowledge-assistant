"""Public persistence construction interfaces."""

from trustworthy_kb.persistence import answer_tables as _answer_tables
from trustworthy_kb.persistence import audit_tables as _audit_tables
from trustworthy_kb.persistence import governance_tables as _governance_tables
from trustworthy_kb.persistence import ingestion_tables as _ingestion_tables
from trustworthy_kb.persistence import knowledge_tables as _knowledge_tables
from trustworthy_kb.persistence import publication_tables as _publication_tables
from trustworthy_kb.persistence import source_tables as _source_tables
from trustworthy_kb.persistence.base import Base
from trustworthy_kb.persistence.database import create_database_engine, create_session_factory
from trustworthy_kb.persistence.unit_of_work import SqliteUnitOfWork, SqliteUnitOfWorkFactory

_REGISTERED_TABLE_MODULES = (
    _answer_tables,
    _audit_tables,
    _ingestion_tables,
    _governance_tables,
    _knowledge_tables,
    _publication_tables,
    _source_tables,
)

__all__ = [
    "Base",
    "SqliteUnitOfWork",
    "SqliteUnitOfWorkFactory",
    "create_database_engine",
    "create_session_factory",
]
