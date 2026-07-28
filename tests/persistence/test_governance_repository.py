from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    ActorType,
    ChangeType,
    ClaimId,
    ClaimRecord,
    ClaimStatus,
    ClaimType,
    GovernanceItemId,
    GovernanceItemRecord,
    GovernanceItemStage,
    GovernanceRunId,
    GovernanceRunRecord,
    GovernanceRunStatus,
    KnowledgeChangeId,
    KnowledgeChangeRecord,
    KnowledgeChangeStatus,
    QualityCheckId,
    QualityCheckRecord,
    QualityVerdict,
    ReviewRequestId,
    ReviewRequestRecord,
    ReviewRequestStatus,
    RiskLevel,
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
from trustworthy_kb.persistence.governance_repository import GovernanceRepository
from trustworthy_kb.persistence.knowledge_repository import KnowledgeRepository
from trustworthy_kb.persistence.publication_repository import PublicationRepository
from trustworthy_kb.persistence.source_repository import SourceRepository


async def seeded_repositories(tmp_path: Path) -> tuple[object, object, dict[str, object]]:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'governance.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    sources = SourceRepository(session)
    publication = PublicationRepository(session)
    knowledge = KnowledgeRepository(session)
    timestamp = datetime.now(UTC)
    source_id = SourceId.generate()
    version_id = SourceVersionId.generate()
    change_id = KnowledgeChangeId.generate()
    claim_id = ClaimId.generate()
    await sources.add_source(
        SourceRecord(
            id=source_id,
            source_type=SourceType.USER_INPUT,
            canonical_uri=f"user://{source_id}",
            owner="test",
            trust_tier=TrustTier.T0,
            sensitivity=Sensitivity.PRIVATE,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    await sources.append_source_version(
        SourceVersionRecord(
            id=version_id,
            source_id=source_id,
            version_number=1,
            content_hash="a" * 64,
            byte_size=1,
            media_type="text/markdown",
            captured_at=timestamp,
            original_path="note.md",
            status=SourceVersionStatus.CAPTURED,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    parsed = await sources.transition_source_version(
        version_id, SourceVersionStatus.PARSED, expected_revision=1
    )
    await sources.transition_source_version(
        version_id, SourceVersionStatus.READY, expected_revision=parsed.revision
    )
    await publication.add_knowledge_change(
        KnowledgeChangeRecord(
            id=change_id,
            source_id=source_id,
            target_version_id=version_id,
            change_type=ChangeType.CREATED,
            diff_hash="b" * 64,
            diff_summary_json={},
            status=KnowledgeChangeStatus.RECEIVED,
            operation_id="governance-test",
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    await knowledge.add_claim(
        ClaimRecord(
            id=claim_id,
            claim_fingerprint=hashlib.sha256(b"claim").hexdigest(),
            claim_family_key=hashlib.sha256(b"family").hexdigest(),
            claim_type=ClaimType.FACT,
            subject="subject",
            predicate="is",
            object_json={"value": True},
            scope_json={},
            sensitivity=Sensitivity.PRIVATE,
            status=ClaimStatus.PROPOSED,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    return (
        engine,
        session,
        {
            "timestamp": timestamp,
            "version_id": version_id,
            "change_id": change_id,
            "claim_id": claim_id,
        },
    )


def run_record(values: dict[str, object]) -> GovernanceRunRecord:
    timestamp = values["timestamp"]
    return GovernanceRunRecord(
        id=GovernanceRunId.generate(),
        knowledge_change_id=values["change_id"],
        target_source_version_id=values["version_id"],
        policy_version="l3-v1",
        extractor_version="extractor-v1",
        verifier_version="verifier-v1",
        search_policy_version="search-v1",
        status=GovernanceRunStatus.PLANNING,
        revision=1,
        started_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_governance_repository_persists_run_item_and_cas_transitions(
    tmp_path: Path,
) -> None:
    engine, session, values = await seeded_repositories(tmp_path)
    repository = GovernanceRepository(session)
    run = run_record(values)
    try:
        assert await repository.add_run(run) == run
        assert await repository.get_run_for_change(values["change_id"], "l3-v1") == run
        extracting = await repository.transition_run(
            run.id, GovernanceRunStatus.EXTRACTING, expected_revision=1
        )
        counted = await repository.set_run_counts(
            run.id,
            total=1,
            decided=0,
            review=0,
            failed=0,
            quarantined=0,
            expected_revision=extracting.revision,
        )
        item = GovernanceItemRecord(
            id=GovernanceItemId.generate(),
            run_id=run.id,
            claim_id=values["claim_id"],
            stage=GovernanceItemStage.EXTRACTED,
            attempt=1,
            risk_level=RiskLevel.LOW,
            revision=1,
            created_at=values["timestamp"],
            updated_at=values["timestamp"],
        )
        assert await repository.add_item(item) == item
        pending = await repository.transition_item(
            item.id, GovernanceItemStage.EVIDENCE_PENDING, expected_revision=1
        )
        stored = await repository.set_item_artifacts(
            item.id,
            search_manifest_hash="c" * 64,
            evidence_pack_hash="d" * 64,
            expected_revision=pending.revision,
        )

        quality = QualityCheckRecord(
            id=QualityCheckId.generate(),
            claim_id=values["claim_id"],
            policy_version="l3-v1",
            verdict=QualityVerdict.INSUFFICIENT,
            dimensions_json={},
            reason_code="NO_EVIDENCE",
            reason_summary="No evidence was available.",
            evidence_snapshot_hash="e" * 64,
            created_at=values["timestamp"],
        )
        await KnowledgeRepository(session).record_quality_check(quality, ())
        pointed = await repository.set_item_quality_check(
            item.id,
            quality.id,
            expected_revision=stored.revision,
        )

        assert stored.evidence_pack_hash == "d" * 64
        assert pointed.current_quality_check_id == quality.id
        assert await repository.list_items(run.id) == (pointed,)
        assert counted.total_items == 1
        with pytest.raises(ConcurrentModificationError):
            await repository.transition_run(
                run.id, GovernanceRunStatus.EVALUATING, expected_revision=1
            )
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_governance_repository_creates_one_live_review_and_records_decision(
    tmp_path: Path,
) -> None:
    engine, session, values = await seeded_repositories(tmp_path)
    governance = GovernanceRepository(session)
    quality_id = QualityCheckId.generate()
    quality = QualityCheckRecord(
        id=quality_id,
        claim_id=values["claim_id"],
        policy_version="l3-v1",
        verdict=QualityVerdict.INSUFFICIENT,
        dimensions_json={},
        reason_code="SEARCH_UNAVAILABLE",
        reason_summary="No independently fetched evidence was available.",
        evidence_snapshot_hash="e" * 64,
        created_at=values["timestamp"],
    )
    await KnowledgeRepository(session).record_quality_check(quality, ())
    request = ReviewRequestRecord(
        id=ReviewRequestId.generate(),
        claim_id=values["claim_id"],
        quality_check_id=quality_id,
        knowledge_change_id=values["change_id"],
        risk_level=RiskLevel.MEDIUM,
        reason_code="SEARCH_UNAVAILABLE",
        status=ReviewRequestStatus.PENDING,
        revision=1,
        created_at=values["timestamp"],
        updated_at=values["timestamp"],
    )
    try:
        assert await governance.add_review_request(request) == request
        assert await governance.get_review_request(request.id) == request
        assert await governance.list_pending_reviews() == (request,)
        await session.commit()
        duplicate = request.model_copy(update={"id": ReviewRequestId.generate()})
        with pytest.raises(DuplicateRecordError):
            await governance.add_review_request(duplicate)
        await session.rollback()

        decided = await governance.decide_review(
            request.id,
            ReviewRequestStatus.APPROVED,
            decision_reason_code="USER_CONFIRMED",
            actor_type=ActorType.USER,
            expected_revision=1,
        )
        assert decided.status is ReviewRequestStatus.APPROVED
        assert decided.decision_actor_type is ActorType.USER
        assert decided.decided_at is not None
        assert await governance.list_pending_reviews() == ()
    finally:
        await session.close()
        await engine.dispose()
