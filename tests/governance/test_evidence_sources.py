from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import SourceType, TrustTier
from trustworthy_kb.governance.contracts import (
    FetchedEvidenceBlock,
    FetchedEvidenceDocument,
)
from trustworthy_kb.governance.evidence_sources import (
    DomainTrustResolver,
    EvidenceSourceService,
)
from trustworthy_kb.persistence import (
    Base,
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)


def _document() -> FetchedEvidenceDocument:
    text = "Synthetic independently fetched evidence."
    digest = hashlib.sha256(text.encode()).hexdigest()
    return FetchedEvidenceDocument(
        normalized_url="https://docs.example.com/reference",
        final_url="https://docs.example.com/reference",
        raw_content_hash=digest,
        normalized_text_hash=digest,
        media_type="text/plain",
        byte_size=len(text),
        captured_at=datetime.now(UTC),
        freshness_metadata_hash="a" * 64,
        complete=True,
        extraction_status="COMPLETE",
        raw_snapshot_ref=f"raw:{digest}",
        extracted_snapshot_ref=f"extracted:{digest}",
        blocks=(FetchedEvidenceBlock(anchor="body", text=text, text_hash=digest),),
    )


def test_domain_trust_resolver_matches_only_host_boundaries() -> None:
    resolver = DomainTrustResolver(t1_domains=("example.com",), t2_domains=("trusted.test",))

    assert resolver.resolve("https://docs.example.com/path") is TrustTier.T1
    assert resolver.resolve("https://example.com.evil.test/path") is TrustTier.T4
    assert resolver.resolve("https://trusted.test/path") is TrustTier.T2


@pytest.mark.asyncio
async def test_evidence_source_service_persists_and_reuses_lineage(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'evidence.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    service = EvidenceSourceService(DomainTrustResolver(t1_domains=("example.com",)))
    try:
        async with factory() as unit_of_work:
            first = await service.persist(unit_of_work, _document())
            await unit_of_work.commit()
        async with factory() as unit_of_work:
            second = await service.persist(unit_of_work, _document())
            source = await unit_of_work.sources.get_source(second.version.source_id)
            blocks = await unit_of_work.sources.list_content_blocks(second.version.id)
            await unit_of_work.commit()

        assert first.version.id == second.version.id
        assert first.family.id == second.family.id
        assert source.source_type is SourceType.OFFICIAL_DOCUMENTATION
        assert source.trust_tier is TrustTier.T1
        assert len(blocks) == 1
    finally:
        await engine.dispose()
