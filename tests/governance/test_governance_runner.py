from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.config import DatabaseSettings, GovernanceSettings, SearchSettings
from trustworthy_kb.domain import (
    ChangeType,
    ClaimStatus,
    ClaimType,
    ContentBlockId,
    ContentBlockRecord,
    EvidenceStance,
    KnowledgeChangeId,
    KnowledgeChangeRecord,
    KnowledgeChangeStatus,
    ReviewRequestStatus,
    Sensitivity,
    SourceId,
    SourceRecord,
    SourceType,
    SourceVersionId,
    SourceVersionRecord,
    SourceVersionStatus,
    TrustTier,
)
from trustworthy_kb.governance import (
    ClaimDraft,
    ClaimObject,
    ClaimOriginSpan,
    ClaimScope,
    EvidencePackBuilder,
    EvidenceSearchHit,
    EvidenceSearchRequest,
    EvidenceSnapshotStore,
    EvidenceVerificationOutput,
    QualityPolicyEngine,
    SearchIntent,
    SnapshotContentResolver,
)
from trustworthy_kb.governance.contracts import (
    CandidateVerification,
    FetchedEvidenceBlock,
    FetchedEvidenceDocument,
)
from trustworthy_kb.governance.errors import SearchCapabilityUnavailableError
from trustworthy_kb.governance.evidence_sources import (
    DomainTrustResolver,
    EvidenceSourceService,
)
from trustworthy_kb.governance.review import ReviewService
from trustworthy_kb.governance.runner import ClaimGovernanceRunner
from trustworthy_kb.governance.workflow import run_governance_workflow
from trustworthy_kb.ingestion import ContentAddressedSnapshotStore, MarkdownBlockParser
from trustworthy_kb.persistence import (
    Base,
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)


class FakeExtractor:
    def __init__(self, draft: ClaimDraft) -> None:
        self.draft = draft

    async def extract(self, _content: object) -> tuple[ClaimDraft, ...]:
        return (self.draft,)


class UnavailableSearch:
    async def search(self, _request: object) -> tuple[object, ...]:
        raise SearchCapabilityUnavailableError("synthetic unavailable")

    def capabilities(self) -> object:
        return object()


class UnusedFetcher:
    async def fetch(self, _url: str) -> object:
        raise AssertionError("fetch must not be called")


class EmptyVerifier:
    async def verify(self, _claim: object, _candidates: object) -> EvidenceVerificationOutput:
        return EvidenceVerificationOutput(results=())


class SuccessfulSearch:
    async def search(self, request: EvidenceSearchRequest) -> tuple[EvidenceSearchHit, ...]:
        host = "official.example" if request.intent is SearchIntent.SUPPORT else "challenge.example"
        return (
            EvidenceSearchHit(
                candidate_id=f"candidate-{request.intent.value.lower()}",
                url=f"https://{host}/reference",
                title="Synthetic reference",
                provider_request_id=f"resp-{request.intent.value.lower()}",
                rank=0,
            ),
        )

    def capabilities(self) -> object:
        return object()


class SuccessfulFetcher:
    async def fetch(self, url: str) -> FetchedEvidenceDocument:
        text = f"Authoritative evidence for {url}"
        digest = hashlib.sha256(text.encode()).hexdigest()
        return FetchedEvidenceDocument(
            normalized_url=url,
            final_url=url,
            raw_content_hash=digest,
            normalized_text_hash=digest,
            media_type="text/plain",
            byte_size=len(text),
            captured_at=datetime.now(UTC),
            freshness_metadata_hash="f" * 64,
            complete=True,
            extraction_status="COMPLETE",
            raw_snapshot_ref=f"raw:{digest}",
            extracted_snapshot_ref=f"extracted:{digest}",
            blocks=(FetchedEvidenceBlock(anchor="body", text=text, text_hash=digest),),
        )


class SupportingVerifier:
    async def verify(
        self, _claim: object, candidates: tuple[object, ...]
    ) -> EvidenceVerificationOutput:
        return EvidenceVerificationOutput(
            results=tuple(
                CandidateVerification(
                    candidate_id=candidate.candidate_id,  # type: ignore[attr-defined]
                    stance=EvidenceStance.SUPPORTS,
                    supported_claim_fields=("subject", "predicate", "object"),
                    evidence_coverage=1,
                    scope_match=True,
                    version_match=True,
                    freshness_match=True,
                    relevance=1,
                    reason_codes=("DIRECT_SUPPORT",),
                )
                for candidate in candidates
            )
        )


async def _seed(
    tmp_path: Path,
) -> tuple[object, SqliteUnitOfWorkFactory, KnowledgeChangeId, str]:
    database_path = tmp_path / "governance-runner.db"
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    raw = b"# Profile\n\nI prefer deterministic tools.\n"
    digest = hashlib.sha256(raw).hexdigest()
    parsed = MarkdownBlockParser().parse(raw.decode())
    timestamp = datetime.now(UTC)
    source_id = SourceId.generate()
    version_id = SourceVersionId.generate()
    change_id = KnowledgeChangeId.generate()
    async with factory() as unit_of_work:
        source = await unit_of_work.sources.add_source(
            SourceRecord(
                id=source_id,
                source_type=SourceType.OBSIDIAN_MARKDOWN,
                canonical_uri="obsidian://synthetic/profile.md",
                owner="me",
                trust_tier=TrustTier.T0,
                sensitivity=Sensitivity.PRIVATE,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        version = await unit_of_work.sources.append_source_version(
            SourceVersionRecord(
                id=version_id,
                source_id=source_id,
                version_number=1,
                content_hash=digest,
                byte_size=len(raw),
                media_type="text/markdown",
                captured_at=timestamp,
                original_path="profile.md",
                status=SourceVersionStatus.CAPTURED,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await unit_of_work.sources.add_content_blocks(
            tuple(
                ContentBlockRecord(
                    id=ContentBlockId.generate(),
                    source_version_id=version.id,
                    ordinal=block.ordinal,
                    block_type=block.block_type,
                    anchor=block.anchor,
                    text_hash=block.text_hash,
                    character_count=block.character_count,
                    created_at=timestamp,
                )
                for block in parsed.blocks
            )
        )
        parsed_version = await unit_of_work.sources.transition_source_version(
            version.id, SourceVersionStatus.PARSED, expected_revision=version.revision
        )
        await unit_of_work.sources.transition_source_version(
            version.id, SourceVersionStatus.READY, expected_revision=parsed_version.revision
        )
        await unit_of_work.sources.activate_source_version(
            source.id, version.id, expected_revision=source.revision
        )
        await unit_of_work.publication.add_knowledge_change(
            KnowledgeChangeRecord(
                id=change_id,
                source_id=source.id,
                target_version_id=version.id,
                change_type=ChangeType.CREATED,
                diff_hash="a" * 64,
                diff_summary_json={"kind": "synthetic"},
                status=KnowledgeChangeStatus.RECEIVED,
                operation_id="runner-test",
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await unit_of_work.commit()
    await ContentAddressedSnapshotStore(tmp_path / "source-snapshots").put(raw, digest)
    return engine, factory, change_id, parsed.blocks[1].anchor


def _draft(anchor: str, claim_type: ClaimType, sensitivity: Sensitivity) -> ClaimDraft:
    return ClaimDraft(
        claim_type=claim_type,
        subject="me" if claim_type is ClaimType.PREFERENCE else "Python",
        predicate="prefers" if claim_type is ClaimType.PREFERENCE else "is",
        object=ClaimObject(
            value="deterministic tools" if claim_type is ClaimType.PREFERENCE else "a language",
            value_type="text",
        ),
        scope=ClaimScope(owner="me" if claim_type is ClaimType.PREFERENCE else None),
        sensitivity=sensitivity,
        origins=(ClaimOriginSpan(block_anchor=anchor, start=0, end=5),),
    )


def _runner(
    tmp_path: Path,
    factory: SqliteUnitOfWorkFactory,
    draft: ClaimDraft,
) -> ClaimGovernanceRunner:
    store = EvidenceSnapshotStore(tmp_path / "evidence-snapshots")
    return ClaimGovernanceRunner(
        unit_of_work_factory=factory,
        settings=GovernanceSettings(),
        search_settings=SearchSettings(),
        resolver=SnapshotContentResolver(tmp_path / "source-snapshots"),
        extractor=FakeExtractor(draft),  # type: ignore[arg-type]
        search=UnavailableSearch(),  # type: ignore[arg-type]
        fetcher=UnusedFetcher(),  # type: ignore[arg-type]
        evidence_sources=EvidenceSourceService(DomainTrustResolver()),
        pack_builder=EvidencePackBuilder(store),
        verifier=EmptyVerifier(),  # type: ignore[arg-type]
        policy=QualityPolicyEngine(),
        snapshot_store=store,
    )


def _verified_runner(
    tmp_path: Path,
    factory: SqliteUnitOfWorkFactory,
    draft: ClaimDraft,
) -> ClaimGovernanceRunner:
    store = EvidenceSnapshotStore(tmp_path / "evidence-snapshots")
    return ClaimGovernanceRunner(
        unit_of_work_factory=factory,
        settings=GovernanceSettings(t1_domains=("official.example", "challenge.example")),
        search_settings=SearchSettings(),
        resolver=SnapshotContentResolver(tmp_path / "source-snapshots"),
        extractor=FakeExtractor(draft),  # type: ignore[arg-type]
        search=SuccessfulSearch(),  # type: ignore[arg-type]
        fetcher=SuccessfulFetcher(),  # type: ignore[arg-type]
        evidence_sources=EvidenceSourceService(
            DomainTrustResolver(t1_domains=("official.example", "challenge.example"))
        ),
        pack_builder=EvidencePackBuilder(store),
        verifier=SupportingVerifier(),  # type: ignore[arg-type]
        policy=QualityPolicyEngine(),
        snapshot_store=store,
    )


@pytest.mark.asyncio
async def test_runner_auto_decides_owner_scoped_preference_without_web_disclosure(
    tmp_path: Path,
) -> None:
    engine, factory, change_id, anchor = await _seed(tmp_path)
    try:
        report = await _runner(
            tmp_path, factory, _draft(anchor, ClaimType.PREFERENCE, Sensitivity.PRIVATE)
        ).run_change(change_id)
        async with factory() as unit_of_work:
            change = await unit_of_work.publication.get_knowledge_change(change_id)
            claims = await unit_of_work.knowledge.list_claims_for_source_version(
                change.target_version_id
            )

        assert report.decided == 1 and report.review == 0
        assert change.status is KnowledgeChangeStatus.PUBLISH_INTENT
        assert claims[0].status is ClaimStatus.USER_ASSERTED
    finally:
        await engine.dispose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_runner_routes_search_capability_gap_to_review_instead_of_failure(
    tmp_path: Path,
) -> None:
    engine, factory, change_id, anchor = await _seed(tmp_path)
    try:
        report = await _runner(
            tmp_path, factory, _draft(anchor, ClaimType.FACT, Sensitivity.PUBLIC)
        ).run_change(change_id)
        async with factory() as unit_of_work:
            change = await unit_of_work.publication.get_knowledge_change(change_id)
            reviews = await unit_of_work.governance.list_pending_reviews()
            claims = await unit_of_work.knowledge.list_claims_for_source_version(
                change.target_version_id
            )

        assert report.review == 1 and report.failed == 0
        assert change.status is KnowledgeChangeStatus.REVIEW_REQUIRED
        assert claims[0].status is ClaimStatus.INSUFFICIENT
        assert len(reviews) == 1
    finally:
        await engine.dispose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_runner_persists_independent_evidence_and_auto_verifies_low_risk_claim(
    tmp_path: Path,
) -> None:
    engine, factory, change_id, anchor = await _seed(tmp_path)
    try:
        report = await _verified_runner(
            tmp_path, factory, _draft(anchor, ClaimType.FACT, Sensitivity.PUBLIC)
        ).run_change(change_id)
        async with factory() as unit_of_work:
            change = await unit_of_work.publication.get_knowledge_change(change_id)
            claims = await unit_of_work.knowledge.list_claims_for_source_version(
                change.target_version_id
            )

        assert report.decided == 1 and report.failed == 0
        assert change.status is KnowledgeChangeStatus.PUBLISH_INTENT
        assert claims[0].status is ClaimStatus.VERIFIED
        assert claims[0].current_quality_check_id is not None
    finally:
        await engine.dispose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_review_approval_releases_change_after_last_pending_request(
    tmp_path: Path,
) -> None:
    engine, factory, change_id, anchor = await _seed(tmp_path)
    try:
        await _runner(
            tmp_path, factory, _draft(anchor, ClaimType.FACT, Sensitivity.PUBLIC)
        ).run_change(change_id)
        service = ReviewService(factory)
        request = (await service.list_pending())[0]

        decided = await service.decide(
            request.id, ReviewRequestStatus.APPROVED, reason_code="HUMAN_CONFIRMED"
        )
        async with factory() as unit_of_work:
            change = await unit_of_work.publication.get_knowledge_change(change_id)

        assert decided.status is ReviewRequestStatus.APPROVED
        assert change.status is KnowledgeChangeStatus.PUBLISH_INTENT
    finally:
        await engine.dispose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_governance_workflow_checkpoints_only_summary_and_resumes(tmp_path: Path) -> None:
    engine, factory, change_id, anchor = await _seed(tmp_path)
    runner = _runner(tmp_path, factory, _draft(anchor, ClaimType.PREFERENCE, Sensitivity.PRIVATE))
    checkpoint = tmp_path / "checkpoints" / "governance.sqlite"
    try:
        first = await run_governance_workflow(runner, checkpoint, change_id=change_id)
        resumed = await run_governance_workflow(runner, checkpoint, change_id=change_id)

        assert resumed == first
        checkpoint_bytes = checkpoint.read_bytes()
        assert b"deterministic tools" not in checkpoint_bytes
        assert b"TRUSTKB_LLM_API_KEY" not in checkpoint_bytes
    finally:
        await engine.dispose()  # type: ignore[union-attr]
