"""Transactional L3 runner from source change to governed claim decisions."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from trustworthy_kb.config import GovernanceSettings, SearchSettings
from trustworthy_kb.domain import (
    ChangeType,
    ClaimId,
    ClaimOriginRecord,
    ClaimRecord,
    ClaimStatus,
    ContentBlockRecord,
    EvidenceFamilyId,
    EvidenceId,
    EvidenceRecord,
    GovernanceItemId,
    GovernanceItemRecord,
    GovernanceItemStage,
    GovernanceRunId,
    GovernanceRunRecord,
    GovernanceRunStatus,
    KnowledgeChangeId,
    KnowledgeChangeRecord,
    KnowledgeChangeStatus,
    QualityCheckEvidenceRecord,
    QualityCheckId,
    QualityCheckRecord,
    ReviewRequestId,
    ReviewRequestRecord,
    ReviewRequestStatus,
    SourceRecord,
    SourceVersionRecord,
    can_transition,
)
from trustworthy_kb.governance.contracts import (
    ClaimDraft,
    EvidenceSearchHit,
    EvidenceSearchRequest,
    EvidenceVerificationOutput,
    FetchedEvidenceDocument,
    PublicClaim,
    SearchIntent,
)
from trustworthy_kb.governance.errors import (
    EvidenceFetchError,
    GovernanceError,
    SearchCapabilityUnavailableError,
    UnsafeFetchTargetError,
)
from trustworthy_kb.governance.evidence_pack import (
    EvidenceMaterial,
    EvidencePackBuilder,
    StoredEvidencePack,
    store_search_manifest,
)
from trustworthy_kb.governance.evidence_sources import EvidenceSourceService
from trustworthy_kb.governance.extraction import ClaimExtractor, SnapshotContentResolver
from trustworthy_kb.governance.fetch import SecureWebFetcher
from trustworthy_kb.governance.fingerprints import (
    canonical_json_hash,
    claim_family_key,
    claim_fingerprint,
)
from trustworthy_kb.governance.quality import PolicyDecision, QualityPolicyEngine, classify_risk
from trustworthy_kb.governance.search import EvidenceSearchGateway
from trustworthy_kb.governance.snapshot_store import EvidenceSnapshotStore
from trustworthy_kb.governance.verifier import EvidenceVerifier
from trustworthy_kb.persistence import SqliteUnitOfWork, SqliteUnitOfWorkFactory
from trustworthy_kb.persistence.base import utc_now

_TERMINAL_RUNS = frozenset(
    {
        GovernanceRunStatus.COMPLETED,
        GovernanceRunStatus.PARTIAL_FAILED,
        GovernanceRunStatus.FAILED,
        GovernanceRunStatus.QUARANTINED,
    }
)
_PUBLIC_FACT_TYPES = frozenset(
    {
        "FACT",
        "DEFINITION",
        "PROCEDURE",
        "CODE_BEHAVIOR",
    }
)


class GovernanceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: GovernanceRunId
    change_id: KnowledgeChangeId
    status: GovernanceRunStatus
    total: int
    decided: int
    review: int
    failed: int
    quarantined: int


@dataclass(frozen=True, slots=True)
class _PendingClaim:
    draft: ClaimDraft
    claim: ClaimRecord
    item: GovernanceItemRecord


@dataclass(frozen=True, slots=True)
class _EvidenceResult:
    stored_pack: StoredEvidencePack
    verification: EvidenceVerificationOutput
    materials: tuple[EvidenceMaterial, ...]
    family_ids: dict[str, EvidenceFamilyId]
    search_manifest_hash: str


class ClaimGovernanceRunner:
    """Execute the frozen L3 policy while keeping network content out of SQLite."""

    def __init__(
        self,
        *,
        unit_of_work_factory: SqliteUnitOfWorkFactory,
        settings: GovernanceSettings,
        search_settings: SearchSettings,
        resolver: SnapshotContentResolver,
        extractor: ClaimExtractor,
        search: EvidenceSearchGateway,
        fetcher: SecureWebFetcher,
        evidence_sources: EvidenceSourceService,
        pack_builder: EvidencePackBuilder,
        verifier: EvidenceVerifier,
        policy: QualityPolicyEngine,
        snapshot_store: EvidenceSnapshotStore,
    ) -> None:
        self._uow_factory = unit_of_work_factory
        self._settings = settings
        self._search_settings = search_settings
        self._resolver = resolver
        self._extractor = extractor
        self._search = search
        self._fetcher = fetcher
        self._evidence_sources = evidence_sources
        self._pack_builder = pack_builder
        self._verifier = verifier
        self._policy = policy
        self._snapshot_store = snapshot_store

    async def run_pending(self) -> tuple[GovernanceReport, ...]:
        change_ids = await self.pending_change_ids()
        return tuple([await self.run_change(change_id) for change_id in change_ids])

    async def pending_change_ids(self) -> tuple[KnowledgeChangeId, ...]:
        async with self._uow_factory() as unit_of_work:
            changes = await unit_of_work.publication.list_knowledge_changes(
                KnowledgeChangeStatus.RECEIVED
            )
        return tuple(change.id for change in changes)

    async def run_change(self, change_id: KnowledgeChangeId) -> GovernanceReport:
        change, source, version, blocks, run = await self._begin(change_id)
        if run.status in _TERMINAL_RUNS:
            return _report(run)
        if change.change_type in {ChangeType.MOVED, ChangeType.DELETED}:
            await self._apply_non_content_change(change, run)
            return await self.reconcile(run.id)
        try:
            content = await self._resolver.resolve(version, blocks)
            drafts = await self._extractor.extract(content)
            pending = await self._persist_extraction(run, source, blocks, drafts)
        except Exception as error:
            await self._fail_run(run.id, change.id, _error_category(error))
            raise

        for pending_claim in pending:
            try:
                await self._evaluate_claim(change, source, pending_claim)
            except Exception as error:
                await self._fail_item(pending_claim.item.id, _error_category(error))
        return await self.reconcile(run.id)

    async def reconcile(self, run_id: GovernanceRunId) -> GovernanceReport:
        async with self._uow_factory() as unit_of_work:
            run = await unit_of_work.governance.get_run(run_id)
            if run.status in _TERMINAL_RUNS:
                return _report(run)
            items = tuple(await unit_of_work.governance.list_items(run_id))
            claims = {
                item.claim_id: await unit_of_work.knowledge.get_claim(item.claim_id)
                for item in items
            }
            quarantined = sum(
                claims[item.claim_id].status is ClaimStatus.QUARANTINED for item in items
            )
            review = sum(item.stage is GovernanceItemStage.REVIEW_REQUIRED for item in items)
            failed = sum(item.stage is GovernanceItemStage.FAILED for item in items)
            decided = sum(item.stage is GovernanceItemStage.DECIDED for item in items) - quarantined
            if run.status is GovernanceRunStatus.EVALUATING:
                run = await unit_of_work.governance.transition_run(
                    run.id,
                    GovernanceRunStatus.RECONCILING,
                    expected_revision=run.revision,
                )
            run = await unit_of_work.governance.set_run_counts(
                run.id,
                total=len(items),
                decided=decided,
                review=review,
                failed=failed,
                quarantined=quarantined,
                expected_revision=run.revision,
            )
            terminal = _terminal_run_status(len(items), failed, quarantined)
            run = await unit_of_work.governance.transition_run(
                run.id, terminal, expected_revision=run.revision
            )
            change = await unit_of_work.publication.get_knowledge_change(run.knowledge_change_id)
            target_change_status = _change_status(failed, review, quarantined, len(items))
            if change.status is KnowledgeChangeStatus.VALIDATING:
                await unit_of_work.publication.transition_knowledge_change(
                    change.id, target_change_status, expected_revision=change.revision
                )
            await unit_of_work.commit()
            return _report(run)

    async def _begin(
        self, change_id: KnowledgeChangeId
    ) -> tuple[
        KnowledgeChangeRecord,
        SourceRecord,
        SourceVersionRecord,
        tuple[ContentBlockRecord, ...],
        GovernanceRunRecord,
    ]:
        async with self._uow_factory() as unit_of_work:
            change = await unit_of_work.publication.get_knowledge_change(change_id)
            existing = await unit_of_work.governance.get_run_for_change(
                change.id, self._settings.policy_version
            )
            source = await unit_of_work.sources.get_source(change.source_id)
            version = await unit_of_work.sources.get_source_version(change.target_version_id)
            blocks = await unit_of_work.sources.list_content_blocks(version.id)
            if existing is not None:
                return change, source, version, blocks, existing
            if change.status is not KnowledgeChangeStatus.RECEIVED:
                raise GovernanceError("knowledge change is not available for governance")
            change = await unit_of_work.publication.transition_knowledge_change(
                change.id,
                KnowledgeChangeStatus.VALIDATING,
                expected_revision=change.revision,
            )
            timestamp = utc_now()
            run = await unit_of_work.governance.add_run(
                GovernanceRunRecord(
                    id=GovernanceRunId.generate(),
                    knowledge_change_id=change.id,
                    target_source_version_id=version.id,
                    policy_version=self._settings.policy_version,
                    extractor_version=self._settings.extractor_version,
                    verifier_version=self._settings.verifier_version,
                    search_policy_version=self._settings.search_policy_version,
                    status=GovernanceRunStatus.PLANNING,
                    revision=1,
                    started_at=timestamp,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            run = await unit_of_work.governance.transition_run(
                run.id, GovernanceRunStatus.EXTRACTING, expected_revision=run.revision
            )
            await unit_of_work.commit()
            return change, source, version, blocks, run

    async def _persist_extraction(
        self,
        run: GovernanceRunRecord,
        source: SourceRecord,
        blocks: tuple[ContentBlockRecord, ...],
        drafts: tuple[ClaimDraft, ...],
    ) -> tuple[_PendingClaim, ...]:
        block_by_anchor = {block.anchor: block for block in blocks}
        unique = {claim_fingerprint(draft): draft for draft in drafts}
        pending: list[_PendingClaim] = []
        async with self._uow_factory() as unit_of_work:
            for fingerprint, draft in unique.items():
                claim = await unit_of_work.knowledge.find_active_claim_by_fingerprint(fingerprint)
                timestamp = utc_now()
                if claim is None:
                    claim = await unit_of_work.knowledge.add_claim(
                        ClaimRecord(
                            id=ClaimId.generate(),
                            claim_fingerprint=fingerprint,
                            claim_family_key=claim_family_key(draft),
                            claim_type=draft.claim_type,
                            subject=draft.subject,
                            predicate=draft.predicate,
                            object_json=draft.object.model_dump(mode="json"),
                            scope_json=draft.scope.model_dump(mode="json"),
                            valid_from=draft.valid_from,
                            valid_to=draft.valid_to,
                            freshness_at=draft.freshness_at,
                            sensitivity=draft.sensitivity,
                            status=ClaimStatus.PROPOSED,
                            revision=1,
                            created_at=timestamp,
                            updated_at=timestamp,
                        )
                    )
                origins_by_anchor: dict[str, list[dict[str, int | str]]] = {}
                for origin in draft.origins:
                    origins_by_anchor.setdefault(origin.block_anchor, []).append(
                        origin.model_dump(mode="json")
                    )
                for anchor, spans in origins_by_anchor.items():
                    block = block_by_anchor[anchor]
                    if not await unit_of_work.knowledge.has_claim_origin(claim.id, block.id):
                        await unit_of_work.knowledge.attach_claim_origin(
                            ClaimOriginRecord(
                                claim_id=claim.id,
                                content_block_id=block.id,
                                origin_span_json={"spans": spans},
                                created_at=timestamp,
                            )
                        )
                item = await unit_of_work.governance.add_item(
                    GovernanceItemRecord(
                        id=GovernanceItemId.generate(),
                        run_id=run.id,
                        claim_id=claim.id,
                        stage=GovernanceItemStage.EXTRACTED,
                        attempt=1,
                        risk_level=classify_risk(draft),
                        revision=1,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                pending.append(_PendingClaim(draft=draft, claim=claim, item=item))
            current = await unit_of_work.governance.get_run(run.id)
            current = await unit_of_work.governance.set_run_counts(
                run.id,
                total=len(pending),
                decided=0,
                review=0,
                failed=0,
                quarantined=0,
                expected_revision=current.revision,
            )
            await unit_of_work.governance.transition_run(
                run.id, GovernanceRunStatus.EVALUATING, expected_revision=current.revision
            )
            await unit_of_work.commit()
        return tuple(pending)

    async def _evaluate_claim(
        self,
        change: KnowledgeChangeRecord,
        source: SourceRecord,
        pending: _PendingClaim,
    ) -> None:
        if pending.claim.status is not ClaimStatus.PROPOSED:
            await self._complete_reused_claim(pending)
            return
        evidence: _EvidenceResult | None = None
        search_available = True
        if _requires_public_evidence(pending.draft):
            try:
                evidence = await self._collect_evidence(pending.draft)
            except SearchCapabilityUnavailableError:
                search_available = False
        elif pending.draft.claim_type.value in _PUBLIC_FACT_TYPES:
            search_available = False

        pack = None if evidence is None else evidence.stored_pack.pack
        verification = (
            EvidenceVerificationOutput(results=()) if evidence is None else evidence.verification
        )
        decision = self._policy.evaluate(
            claim=pending.draft,
            origin_trust_tier=source.trust_tier,
            pack=pack,
            verification=verification,
            search_available=search_available,
        )
        await self._persist_decision(change, pending, decision, evidence)

    async def _collect_evidence(self, draft: ClaimDraft) -> _EvidenceResult:
        public_claim = _public_claim(draft)
        requests = tuple(
            _search_request(
                public_claim,
                intent,
                self._settings.search_policy_version,
                self._search_settings.max_candidates_per_claim,
            )
            for intent in (SearchIntent.SUPPORT, SearchIntent.CHALLENGE)
        )
        result_sets = await asyncio.gather(*(self._search.search(request) for request in requests))
        pairs = _deduplicate_hits(requests, result_sets)
        all_hits = tuple(hit for _, hit in pairs)
        manifest_key = canonical_json_hash(
            {"requests": [request.model_dump(mode="json") for request in requests]}
        )
        manifest_hash, _ = store_search_manifest(
            self._snapshot_store, idempotency_hash=manifest_key, hits=all_hits
        )
        fetched = await self._fetch_all(pairs)
        materials: list[EvidenceMaterial] = []
        family_ids: dict[str, EvidenceFamilyId] = {}
        async with self._uow_factory() as unit_of_work:
            for intent, hit, document in fetched:
                persisted = await self._evidence_sources.persist(unit_of_work, document)
                family_ids[str(persisted.version.id)] = persisted.family.id
                materials.append(
                    EvidenceMaterial(
                        search_hit=hit,
                        document=document,
                        source_version_id=persisted.version.id,
                        trust_tier=persisted.trust_tier,
                        evidence_family=persisted.family.canonical_origin,
                        search_intent=intent,
                    )
                )
            await unit_of_work.commit()
        fingerprint = claim_fingerprint(draft)
        stored = self._pack_builder.build(
            claim_fingerprint=fingerprint,
            claim=public_claim,
            search_policy_version=self._settings.search_policy_version,
            query_hash=manifest_key,
            search_result_snapshot_hash=manifest_hash,
            materials=tuple(materials),
        )
        verifier_candidates = self._pack_builder.verifier_candidates(stored, tuple(materials))
        verification = await self._verifier.verify(draft, verifier_candidates)
        return _EvidenceResult(
            stored_pack=stored,
            verification=verification,
            materials=tuple(materials),
            family_ids=family_ids,
            search_manifest_hash=manifest_hash,
        )

    async def _fetch_all(
        self,
        pairs: tuple[tuple[SearchIntent, EvidenceSearchHit], ...],
    ) -> tuple[tuple[SearchIntent, EvidenceSearchHit, FetchedEvidenceDocument], ...]:
        semaphore = asyncio.Semaphore(self._settings.max_concurrency)

        async def fetch_one(
            intent: SearchIntent, hit: EvidenceSearchHit
        ) -> tuple[SearchIntent, EvidenceSearchHit, FetchedEvidenceDocument] | None:
            async with semaphore:
                try:
                    document = await self._fetcher.fetch(str(hit.url))
                except (EvidenceFetchError, UnsafeFetchTargetError):
                    return None
                if set(document.safety_signals) & {
                    "canonical_origin_mismatch",
                    "canonical_link_invalid",
                }:
                    return None
                return intent, hit, document

        results = await asyncio.gather(*(fetch_one(intent, hit) for intent, hit in pairs))
        return tuple(result for result in results if result is not None)

    async def _persist_decision(
        self,
        change: KnowledgeChangeRecord,
        pending: _PendingClaim,
        decision: PolicyDecision,
        evidence: _EvidenceResult | None,
    ) -> None:
        async with self._uow_factory() as unit_of_work:
            item = await unit_of_work.governance.get_item(pending.item.id)
            item = await _advance_to_deciding(unit_of_work, item, evidence is not None)
            if evidence is not None:
                item = await unit_of_work.governance.set_item_artifacts(
                    item.id,
                    search_manifest_hash=evidence.search_manifest_hash,
                    evidence_pack_hash=evidence.stored_pack.pack_hash,
                    expected_revision=item.revision,
                )
            evidence_records = await _persist_evidence_records(
                unit_of_work, pending.claim.id, evidence
            )
            timestamp = utc_now()
            if evidence is None:
                snapshot_hash, _ = self._snapshot_store.put_json(
                    "packs",
                    {
                        "claim_fingerprint": pending.claim.claim_fingerprint,
                        "verification": [],
                        "reason_code": decision.reason_code,
                    },
                )
            else:
                snapshot_hash = evidence.stored_pack.pack_hash
            quality_id = QualityCheckId.generate()
            quality = await unit_of_work.knowledge.record_quality_check(
                QualityCheckRecord(
                    id=quality_id,
                    claim_id=pending.claim.id,
                    policy_version=self._settings.policy_version,
                    verdict=decision.verdict,
                    dimensions_json=decision.dimensions.model_dump(mode="json"),
                    reason_code=decision.reason_code,
                    reason_summary=decision.reason_summary,
                    evidence_snapshot_hash=snapshot_hash,
                    created_at=timestamp,
                ),
                tuple(
                    QualityCheckEvidenceRecord(
                        quality_check_id=quality_id,
                        evidence_id=record.id,
                        position=position,
                    )
                    for position, record in enumerate(evidence_records)
                ),
            )
            claim = await unit_of_work.knowledge.get_claim(pending.claim.id)
            claim = await unit_of_work.knowledge.set_current_quality_check(
                claim.id, quality.id, expected_revision=claim.revision
            )
            if claim.status is ClaimStatus.PROPOSED:
                await unit_of_work.knowledge.transition_claim(
                    claim.id, decision.claim_status, expected_revision=claim.revision
                )
            item = await unit_of_work.governance.set_item_quality_check(
                item.id, quality.id, expected_revision=item.revision
            )
            terminal = (
                GovernanceItemStage.REVIEW_REQUIRED
                if decision.review_required
                else GovernanceItemStage.DECIDED
            )
            await unit_of_work.governance.transition_item(
                item.id, terminal, expected_revision=item.revision
            )
            if decision.review_required:
                await unit_of_work.governance.add_review_request(
                    ReviewRequestRecord(
                        id=ReviewRequestId.generate(),
                        claim_id=claim.id,
                        quality_check_id=quality.id,
                        knowledge_change_id=change.id,
                        risk_level=decision.risk_level,
                        reason_code=decision.reason_code,
                        status=ReviewRequestStatus.PENDING,
                        revision=1,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
            await unit_of_work.commit()

    async def _complete_reused_claim(self, pending: _PendingClaim) -> None:
        async with self._uow_factory() as unit_of_work:
            item = await unit_of_work.governance.get_item(pending.item.id)
            item = await unit_of_work.governance.transition_item(
                item.id, GovernanceItemStage.DECIDING, expected_revision=item.revision
            )
            if pending.claim.current_quality_check_id is not None:
                item = await unit_of_work.governance.set_item_quality_check(
                    item.id,
                    pending.claim.current_quality_check_id,
                    expected_revision=item.revision,
                )
            await unit_of_work.governance.transition_item(
                item.id, GovernanceItemStage.DECIDED, expected_revision=item.revision
            )
            await unit_of_work.commit()

    async def _apply_non_content_change(
        self, change: KnowledgeChangeRecord, run: GovernanceRunRecord
    ) -> None:
        async with self._uow_factory() as unit_of_work:
            if change.change_type is ChangeType.DELETED:
                version_id = change.base_version_id or change.target_version_id
                claims = await unit_of_work.knowledge.list_claims_for_source_version(version_id)
                for claim in claims:
                    if can_transition(claim.status, ClaimStatus.OUTDATED):
                        await unit_of_work.knowledge.transition_claim(
                            claim.id, ClaimStatus.OUTDATED, expected_revision=claim.revision
                        )
            current = await unit_of_work.governance.get_run(run.id)
            await unit_of_work.governance.transition_run(
                current.id,
                GovernanceRunStatus.EVALUATING,
                expected_revision=current.revision,
            )
            await unit_of_work.commit()

    async def _fail_item(self, item_id: GovernanceItemId, category: str) -> None:
        async with self._uow_factory() as unit_of_work:
            item = await unit_of_work.governance.get_item(item_id)
            if item.stage not in {
                GovernanceItemStage.DECIDED,
                GovernanceItemStage.REVIEW_REQUIRED,
                GovernanceItemStage.FAILED,
            }:
                await unit_of_work.governance.transition_item(
                    item.id,
                    GovernanceItemStage.FAILED,
                    expected_revision=item.revision,
                    error_category=category,
                )
            await unit_of_work.commit()

    async def _fail_run(
        self, run_id: GovernanceRunId, change_id: KnowledgeChangeId, category: str
    ) -> None:
        async with self._uow_factory() as unit_of_work:
            run = await unit_of_work.governance.get_run(run_id)
            if run.status not in _TERMINAL_RUNS:
                await unit_of_work.governance.transition_run(
                    run.id,
                    GovernanceRunStatus.FAILED,
                    expected_revision=run.revision,
                    error_category=category,
                )
            change = await unit_of_work.publication.get_knowledge_change(change_id)
            if can_transition(change.status, KnowledgeChangeStatus.FAILED):
                await unit_of_work.publication.transition_knowledge_change(
                    change.id,
                    KnowledgeChangeStatus.FAILED,
                    expected_revision=change.revision,
                )
            await unit_of_work.commit()


def _requires_public_evidence(claim: ClaimDraft) -> bool:
    return claim.sensitivity.value == "public" and claim.claim_type.value in _PUBLIC_FACT_TYPES


def _public_claim(claim: ClaimDraft) -> PublicClaim:
    return PublicClaim(
        claim_type=claim.claim_type,
        subject=claim.subject,
        predicate=claim.predicate,
        object=claim.object,
        scope=claim.scope,
    )


def _search_request(
    claim: PublicClaim,
    intent: SearchIntent,
    policy_version: str,
    max_results: int,
) -> EvidenceSearchRequest:
    payload = {
        "claim": claim.model_dump(mode="json"),
        "intent": intent.value,
        "policy_version": policy_version,
    }
    return EvidenceSearchRequest(
        claim=claim,
        intent=intent,
        version_constraints=(claim.scope.version,) if claim.scope.version else (),
        scope_constraints=tuple(
            value for value in (claim.scope.domain, claim.scope.geography) if value
        ),
        max_results=max_results,
        policy_version=policy_version,
        idempotency_hash=canonical_json_hash(payload),
    )


def _deduplicate_hits(
    requests: tuple[EvidenceSearchRequest, ...],
    result_sets: list[tuple[EvidenceSearchHit, ...]],
) -> tuple[tuple[SearchIntent, EvidenceSearchHit], ...]:
    result: list[tuple[SearchIntent, EvidenceSearchHit]] = []
    seen: set[tuple[SearchIntent, str]] = set()
    for request, hits in zip(requests, result_sets, strict=True):
        for hit in hits:
            identity = request.intent, str(hit.url)
            if identity not in seen:
                seen.add(identity)
                result.append((request.intent, hit))
    return tuple(result)


async def _advance_to_deciding(
    unit_of_work: SqliteUnitOfWork,
    item: GovernanceItemRecord,
    has_evidence: bool,
) -> GovernanceItemRecord:
    if item.stage is GovernanceItemStage.EXTRACTED and has_evidence:
        item = await unit_of_work.governance.transition_item(
            item.id, GovernanceItemStage.EVIDENCE_PENDING, expected_revision=item.revision
        )
    if item.stage is GovernanceItemStage.EVIDENCE_PENDING:
        item = await unit_of_work.governance.transition_item(
            item.id, GovernanceItemStage.VERIFYING, expected_revision=item.revision
        )
    if item.stage in {GovernanceItemStage.EXTRACTED, GovernanceItemStage.VERIFYING}:
        item = await unit_of_work.governance.transition_item(
            item.id, GovernanceItemStage.DECIDING, expected_revision=item.revision
        )
    return item


async def _persist_evidence_records(
    unit_of_work: SqliteUnitOfWork,
    claim_id: ClaimId,
    evidence: _EvidenceResult | None,
) -> tuple[EvidenceRecord, ...]:
    if evidence is None:
        return ()
    candidates = {item.candidate_id: item for item in evidence.stored_pack.pack.candidates}
    family_counts = Counter(item.evidence_family for item in candidates.values())
    result: list[EvidenceRecord] = []
    for verification in evidence.verification.results:
        candidate = candidates[verification.candidate_id]
        family_id = evidence.family_ids[str(candidate.source_version_id)]
        record = await unit_of_work.knowledge.add_evidence(
            EvidenceRecord(
                id=EvidenceId.generate(),
                claim_id=claim_id,
                source_version_id=candidate.source_version_id,
                evidence_family_id=family_id,
                anchor=candidate.anchor,
                stance=verification.stance,
                excerpt_hash=candidate.excerpt_hash,
                relevance_score=verification.relevance,
                independence_score=1 / family_counts[candidate.evidence_family],
                created_at=utc_now(),
            )
        )
        result.append(record)
    return tuple(result)


def _terminal_run_status(total: int, failed: int, quarantined: int) -> GovernanceRunStatus:
    if total and quarantined == total:
        return GovernanceRunStatus.QUARANTINED
    if total and failed == total:
        return GovernanceRunStatus.FAILED
    if failed:
        return GovernanceRunStatus.PARTIAL_FAILED
    return GovernanceRunStatus.COMPLETED


def _change_status(failed: int, review: int, quarantined: int, total: int) -> KnowledgeChangeStatus:
    if total and quarantined == total:
        return KnowledgeChangeStatus.QUARANTINED
    if failed:
        return KnowledgeChangeStatus.FAILED
    if review:
        return KnowledgeChangeStatus.REVIEW_REQUIRED
    return KnowledgeChangeStatus.PUBLISH_INTENT


def _error_category(error: BaseException) -> str:
    return {
        "EvidencePackIntegrityError": "EVIDENCE_INTEGRITY",
        "ModelOutputValidationError": "MODEL_OUTPUT_INVALID",
        "ModelProviderError": "MODEL_PROVIDER_FAILED",
        "SearchProviderError": "SEARCH_PROVIDER_FAILED",
    }.get(type(error).__name__, "GOVERNANCE_ERROR")


def _report(run: GovernanceRunRecord) -> GovernanceReport:
    return GovernanceReport(
        run_id=run.id,
        change_id=run.knowledge_change_id,
        status=run.status,
        total=run.total_items,
        decided=run.decided_items,
        review=run.review_items,
        failed=run.failed_items,
        quarantined=run.quarantined_items,
    )


__all__ = ["ClaimGovernanceRunner", "GovernanceReport"]
