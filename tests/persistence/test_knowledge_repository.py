from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    ClaimId,
    ClaimOriginRecord,
    ClaimRecord,
    ClaimStatus,
    ClaimType,
    ContentBlockId,
    ContentBlockRecord,
    EvidenceFamilyId,
    EvidenceFamilyRecord,
    EvidenceId,
    EvidenceRecord,
    EvidenceStance,
    InvalidStateTransitionError,
    InvariantViolationError,
    QualityCheckEvidenceRecord,
    QualityCheckId,
    QualityCheckRecord,
    QualityVerdict,
    Sensitivity,
    SourceId,
    SourceRecord,
    SourceType,
    SourceVersionId,
    SourceVersionRecord,
    SourceVersionStatus,
    TrustTier,
)
from trustworthy_kb.persistence import Base, create_database_engine, create_session_factory
from trustworthy_kb.persistence.errors import ConcurrentModificationError, DuplicateRecordError
from trustworthy_kb.persistence.knowledge_repository import KnowledgeRepository
from trustworthy_kb.persistence.source_repository import SourceRepository


def now() -> datetime:
    return datetime.now(UTC)


def claim_record(*, claim_id: ClaimId | None = None, subject: str = "subject") -> ClaimRecord:
    timestamp = now()
    return ClaimRecord(
        id=claim_id or ClaimId.generate(),
        claim_type=ClaimType.FACT,
        subject=subject,
        predicate="is",
        object_json={"hash": "a" * 64},
        scope_json={"kind": "test"},
        sensitivity=Sensitivity.PRIVATE,
        status=ClaimStatus.PROPOSED,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


async def seed_source(repository: SourceRepository) -> tuple[SourceVersionId, ContentBlockId]:
    timestamp = now()
    source_id = SourceId.generate()
    version_id = SourceVersionId.generate()
    block_id = ContentBlockId.generate()
    await repository.add_source(
        SourceRecord(
            id=source_id,
            source_type=SourceType.USER_INPUT,
            canonical_uri=f"user://{source_id}",
            owner="test-user",
            trust_tier=TrustTier.T0,
            sensitivity=Sensitivity.PRIVATE,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    await repository.append_source_version(
        SourceVersionRecord(
            id=version_id,
            source_id=source_id,
            version_number=1,
            content_hash="1" * 64,
            byte_size=1,
            media_type="text/plain",
            captured_at=timestamp,
            original_path="seed.txt",
            status=SourceVersionStatus.CAPTURED,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    parsed = await repository.transition_source_version(
        version_id,
        SourceVersionStatus.PARSED,
        expected_revision=1,
    )
    await repository.transition_source_version(
        version_id,
        SourceVersionStatus.READY,
        expected_revision=parsed.revision,
    )
    await repository.add_content_blocks(
        [
            ContentBlockRecord(
                id=block_id,
                source_version_id=version_id,
                ordinal=0,
                block_type="paragraph",
                anchor="seed",
                text_hash="2" * 64,
                character_count=1,
                created_at=timestamp,
            )
        ]
    )
    return version_id, block_id


@pytest.mark.asyncio
async def test_knowledge_repository_records_evidence_quality_and_claim_lifecycle(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'knowledge.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    repository = KnowledgeRepository(session)
    source_repository = SourceRepository(session)
    try:
        version_id, block_id = await seed_source(source_repository)
        claim = claim_record()
        origin = ClaimOriginRecord(
            claim_id=claim.id,
            content_block_id=block_id,
            origin_span_json={"start": 0, "end": 1},
            created_at=now(),
        )
        family = EvidenceFamilyRecord(
            id=EvidenceFamilyId.generate(),
            canonical_origin="user://primary",
            origin_fingerprint="3" * 64,
            created_at=now(),
        )
        evidence = EvidenceRecord(
            id=EvidenceId.generate(),
            claim_id=claim.id,
            source_version_id=version_id,
            evidence_family_id=family.id,
            anchor="seed",
            stance=EvidenceStance.SUPPORTS,
            excerpt_hash="4" * 64,
            relevance_score=1,
            independence_score=1,
            created_at=now(),
        )
        quality_check = QualityCheckRecord(
            id=QualityCheckId.generate(),
            claim_id=claim.id,
            policy_version="v1",
            verdict=QualityVerdict.VERIFIED,
            dimensions_json={"authority": 1},
            reason_code="SUPPORTED",
            reason_summary="hash-only evidence passed",
            evidence_snapshot_hash="5" * 64,
            created_at=now(),
        )
        quality_evidence = QualityCheckEvidenceRecord(
            quality_check_id=quality_check.id,
            evidence_id=evidence.id,
            position=0,
        )

        assert await repository.add_claim(claim) == claim
        assert await repository.attach_claim_origin(origin) == origin
        assert await repository.add_evidence_family(family) == family
        assert await repository.add_evidence(evidence) == evidence
        assert (
            await repository.record_quality_check(quality_check, [quality_evidence])
            == quality_check
        )

        pointed = await repository.set_current_quality_check(
            claim.id,
            quality_check.id,
            expected_revision=1,
        )
        verified = await repository.transition_claim(
            claim.id,
            ClaimStatus.VERIFIED,
            expected_revision=pointed.revision,
        )
        deleted = await repository.mark_claim_deleted(
            claim.id,
            expected_revision=verified.revision,
        )
        assert pointed.current_quality_check_id == quality_check.id
        assert verified.status is ClaimStatus.VERIFIED
        assert deleted.deleted_at is not None
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_repository_rejects_duplicate_invalid_and_stale_changes(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'knowledge-errors.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    repository = KnowledgeRepository(session)
    try:
        claim = claim_record()
        await repository.add_claim(claim)
        with pytest.raises(DuplicateRecordError):
            await repository.add_claim(claim)
        await session.rollback()

        await repository.add_claim(claim)
        with pytest.raises(InvalidStateTransitionError):
            await repository.transition_claim(
                claim.id,
                ClaimStatus.SUPERSEDED,
                expected_revision=1,
            )
        with pytest.raises(ConcurrentModificationError):
            await repository.mark_claim_deleted(claim.id, expected_revision=99)
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_repository_enforces_quality_check_ownership(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'quality-owner.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    repository = KnowledgeRepository(session)
    try:
        first_claim = claim_record(subject="first")
        second_claim = claim_record(subject="second")
        quality_check = QualityCheckRecord(
            id=QualityCheckId.generate(),
            claim_id=second_claim.id,
            policy_version="v1",
            verdict=QualityVerdict.INSUFFICIENT,
            dimensions_json={},
            reason_code="NO_EVIDENCE",
            reason_summary="no evidence",
            evidence_snapshot_hash="6" * 64,
            created_at=now(),
        )
        await repository.add_claim(first_claim)
        await repository.add_claim(second_claim)
        await repository.record_quality_check(quality_check, [])

        with pytest.raises(InvariantViolationError):
            await repository.set_current_quality_check(
                first_claim.id,
                quality_check.id,
                expected_revision=1,
            )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
