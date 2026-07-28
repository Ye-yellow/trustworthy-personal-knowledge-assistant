"""Restart-safe publication Saga from governed claims to active retrieval state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from trustworthy_kb.domain import (
    ClaimRecord,
    CuratedVersionId,
    CuratedVersionRecord,
    CuratedVersionStatus,
    EntityType,
    IndexGenerationId,
    IndexGenerationRecord,
    IndexGenerationStatus,
    IndexJobId,
    IndexJobRecord,
    IndexJobStatus,
    KnowledgeChangeId,
    KnowledgeChangeRecord,
    KnowledgeChangeStatus,
    KnowledgeNoteId,
    KnowledgeNoteRecord,
    PublicationRunId,
    PublicationRunRecord,
    PublicationRunStatus,
)
from trustworthy_kb.governance.fingerprints import canonical_json_hash
from trustworthy_kb.persistence import SqliteUnitOfWorkFactory
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.publication.chunking import MarkdownChunker
from trustworthy_kb.publication.contracts import CurationArtifact, CurationClaim, KnowledgeChunk
from trustworthy_kb.publication.curation import CuratedMarkdownRenderer, curation_claims
from trustworthy_kb.publication.errors import PublicationError
from trustworthy_kb.publication.indexing import GenerationIndexer
from trustworthy_kb.publication.ports import CurationPlanner
from trustworthy_kb.publication.snapshot_store import PublicationSnapshotStore
from trustworthy_kb.publication.vault import AtomicVaultPublisher


class PublicationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: PublicationRunId
    change_id: KnowledgeChangeId
    note_id: KnowledgeNoteId
    curated_version_id: CuratedVersionId
    generation_id: IndexGenerationId
    status: PublicationRunStatus
    chunk_count: int
    vault_path: str


@dataclass(frozen=True, slots=True)
class _Context:
    run: PublicationRunRecord
    change: KnowledgeChangeRecord
    note: KnowledgeNoteRecord
    version: CuratedVersionRecord
    generation: IndexGenerationRecord
    job: IndexJobRecord
    artifact: CurationArtifact
    claims: tuple[CurationClaim, ...]
    chunks: tuple[KnowledgeChunk, ...]


class PublicationRunner:
    """Execute external writes first and atomically activate their proven identities last."""

    def __init__(
        self,
        *,
        unit_of_work_factory: SqliteUnitOfWorkFactory,
        planner: CurationPlanner,
        renderer: CuratedMarkdownRenderer,
        chunker: MarkdownChunker,
        vault: AtomicVaultPublisher,
        indexer: GenerationIndexer,
        snapshots: PublicationSnapshotStore,
        model_name: str,
        prompt_version: str,
        quality_policy_version: str,
    ) -> None:
        provenance = (model_name.strip(), prompt_version.strip(), quality_policy_version.strip())
        if not all(provenance):
            raise ValueError("publication provenance must not be empty")
        self._uow_factory = unit_of_work_factory
        self._planner = planner
        self._renderer = renderer
        self._chunker = chunker
        self._vault = vault
        self._indexer = indexer
        self._snapshots = snapshots
        self._model_name, self._prompt_version, self._quality_policy_version = provenance

    async def publish(
        self,
        *,
        change_id: KnowledgeChangeId,
        generation_id: IndexGenerationId,
        final_relative_path: str,
        operation_id: str,
    ) -> PublicationReport:
        """Publish or resume one idempotent operation through every Saga phase."""

        if not operation_id.strip():
            raise PublicationError("publication operation ID must not be empty")
        context = await self._begin(
            change_id=change_id,
            generation_id=generation_id,
            final_relative_path=final_relative_path,
            operation_id=operation_id.strip(),
        )
        try:
            while context.run.status is not PublicationRunStatus.COMPLETED:
                if context.run.status is PublicationRunStatus.FAILED:
                    await self._resume_failed(context)
                elif context.run.status is PublicationRunStatus.PLANNING:
                    await self._transition_run(context.run, PublicationRunStatus.CURATING)
                elif context.run.status is PublicationRunStatus.CURATING:
                    await self._stage_vault(context)
                elif context.run.status is PublicationRunStatus.VAULT_STAGED:
                    await self._start_indexing(context)
                elif context.run.status is PublicationRunStatus.INDEXING:
                    await self._finish_indexing(context)
                elif context.run.status is PublicationRunStatus.INDEX_VERIFIED:
                    await self._publish_vault(context)
                elif context.run.status is PublicationRunStatus.VAULT_PUBLISHED:
                    await self._transition_run(context.run, PublicationRunStatus.ACTIVATING)
                elif context.run.status is PublicationRunStatus.ACTIVATING:
                    await self._activate(context)
                else:
                    raise PublicationError("publication run is in an unsupported state")
                context = await self._load(operation_id.strip())
        except Exception as error:
            await self._fail(context.run.id, context.job.id, _error_category(error))
            raise
        return _report(context)

    async def _begin(
        self,
        *,
        change_id: KnowledgeChangeId,
        generation_id: IndexGenerationId,
        final_relative_path: str,
        operation_id: str,
    ) -> _Context:
        async with self._uow_factory() as unit_of_work:
            existing = await unit_of_work.publication.find_publication_run(operation_id)
            if existing is not None:
                existing_version = await unit_of_work.publication.get_curated_version(
                    existing.curated_version_id
                )
                if (
                    existing.knowledge_change_id != change_id
                    or existing.target_generation_id != generation_id
                    or existing_version.vault_path != final_relative_path
                ):
                    raise PublicationError("publication operation ID is bound to another request")
                return await self._load(operation_id)
            change = await unit_of_work.publication.get_knowledge_change(change_id)
            generation = await unit_of_work.publication.get_index_generation(generation_id)
            note = await unit_of_work.publication.find_note_by_path(final_relative_path)
            records = tuple(
                await unit_of_work.knowledge.list_claims_for_source_version(
                    change.target_version_id
                )
            )
            versions = (
                ()
                if note is None
                else await unit_of_work.publication.list_curated_versions(note.id)
            )
        _validate_inputs(change, generation, records)
        claims = curation_claims(records)
        note_id = note.id if note is not None else KnowledgeNoteId.generate()
        version_number = 1 + max((item.version_number for item in versions), default=0)
        version_id = CuratedVersionId.generate()
        plan = await self._planner.plan(claims)
        timestamp = utc_now()
        artifact = self._renderer.render(
            note_id=note_id,
            curated_version_id=version_id,
            based_on_change_id=change.id,
            version_number=version_number,
            plan=plan,
            claims=claims,
            source_ids=(change.source_id,),
            source_version_ids=(change.target_version_id,),
            model_name=self._model_name,
            prompt_version=self._prompt_version,
            quality_policy_version=self._quality_policy_version,
            created_at=timestamp,
        )
        await self._snapshots.put(artifact, claims)
        async with self._uow_factory() as unit_of_work:
            existing = await unit_of_work.publication.find_publication_run(operation_id)
            if existing is not None:
                existing_version = await unit_of_work.publication.get_curated_version(
                    existing.curated_version_id
                )
                if existing_version.vault_path != final_relative_path:
                    raise PublicationError("publication operation ID is bound to another request")
                return await self._load(operation_id)
            current_note = await unit_of_work.publication.find_note_by_path(final_relative_path)
            if (note is None) != (current_note is None) or (
                note is not None
                and current_note is not None
                and note.revision != current_note.revision
            ):
                raise PublicationError("publication note changed during planning")
            if current_note is None:
                current_note = await unit_of_work.publication.add_note(
                    KnowledgeNoteRecord(
                        id=note_id,
                        canonical_path=final_relative_path,
                        revision=1,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
            version = await unit_of_work.publication.add_curated_version(
                CuratedVersionRecord(
                    id=version_id,
                    note_id=current_note.id,
                    version_number=version_number,
                    based_on_change_id=change.id,
                    content_hash=artifact.content_hash,
                    vault_path=final_relative_path,
                    staging_path=self._vault.staging_path(artifact),
                    claim_set_hash=canonical_json_hash([str(item.id) for item in claims]),
                    operation_id=operation_id,
                    status=CuratedVersionStatus.DRAFT,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            await unit_of_work.publication.add_index_job(
                IndexJobRecord(
                    id=IndexJobId.generate(),
                    object_type=EntityType.CURATED_VERSION,
                    object_id=version.id,
                    generation_id=generation.id,
                    status=IndexJobStatus.PENDING,
                    attempt=0,
                    operation_id=operation_id,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            await unit_of_work.publication.add_publication_run(
                PublicationRunRecord(
                    id=PublicationRunId.generate(),
                    knowledge_change_id=change.id,
                    note_id=current_note.id,
                    curated_version_id=version.id,
                    target_generation_id=generation.id,
                    operation_id=operation_id,
                    status=PublicationRunStatus.PLANNING,
                    attempt=1,
                    revision=1,
                    started_at=timestamp,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            await unit_of_work.commit()
        return await self._load(operation_id)

    async def _load(self, operation_id: str) -> _Context:
        async with self._uow_factory() as unit_of_work:
            run = await unit_of_work.publication.find_publication_run(operation_id)
            job = await unit_of_work.publication.find_index_job(operation_id)
            if run is None or job is None:
                raise PublicationError("publication control records are incomplete")
            change = await unit_of_work.publication.get_knowledge_change(run.knowledge_change_id)
            note = await unit_of_work.publication.get_note(run.note_id)
            version = await unit_of_work.publication.get_curated_version(run.curated_version_id)
            generation = await unit_of_work.publication.get_index_generation(
                run.target_generation_id
            )
        snapshot = await self._snapshots.get(version.content_hash)
        artifact = snapshot.artifact
        claims = snapshot.claims
        if (
            artifact.curated_version_id != version.id
            or artifact.note_id != note.id
            or job.object_id != version.id
            or job.generation_id != generation.id
        ):
            raise PublicationError("publication control identities do not agree")
        chunks = self._chunker.chunk(
            artifact,
            claims,
            generation_id=generation.id,
            generation_number=generation.generation_number,
            embedding_model=generation.embedding_model,
        )
        return _Context(run, change, note, version, generation, job, artifact, claims, chunks)

    async def _stage_vault(self, context: _Context) -> None:
        staged = await self._vault.stage(context.artifact)
        await self._vault.verify(staged, expected_hash=context.artifact.content_hash)
        async with self._uow_factory() as unit_of_work:
            run = await unit_of_work.publication.get_publication_run(context.run.id)
            version = await unit_of_work.publication.get_curated_version(context.version.id)
            if version.status is CuratedVersionStatus.DRAFT:
                version = await unit_of_work.publication.transition_curated_version(
                    version.id,
                    CuratedVersionStatus.VALIDATING,
                    expected_revision=version.revision,
                )
            if version.status is CuratedVersionStatus.VALIDATING:
                await unit_of_work.publication.transition_curated_version(
                    version.id,
                    CuratedVersionStatus.STAGING,
                    expected_revision=version.revision,
                )
            await unit_of_work.publication.transition_publication_run(
                run.id,
                PublicationRunStatus.VAULT_STAGED,
                expected_revision=run.revision,
            )
            await unit_of_work.commit()

    async def _start_indexing(self, context: _Context) -> None:
        async with self._uow_factory() as unit_of_work:
            run = await unit_of_work.publication.get_publication_run(context.run.id)
            job = await unit_of_work.publication.get_index_job(context.job.id)
            if job.status is IndexJobStatus.FAILED:
                job = await unit_of_work.publication.transition_index_job(
                    job.id, IndexJobStatus.PENDING, expected_revision=job.revision
                )
            if job.status is IndexJobStatus.PENDING:
                await unit_of_work.publication.transition_index_job(
                    job.id, IndexJobStatus.INDEXING, expected_revision=job.revision
                )
            await unit_of_work.publication.transition_publication_run(
                run.id,
                PublicationRunStatus.INDEXING,
                expected_revision=run.revision,
            )
            await unit_of_work.commit()

    async def _finish_indexing(self, context: _Context) -> None:
        count = await self._indexer.index(context.chunks)
        async with self._uow_factory() as unit_of_work:
            run = await unit_of_work.publication.get_publication_run(context.run.id)
            job = await unit_of_work.publication.get_index_job(context.job.id)
            if job.status is IndexJobStatus.INDEXING:
                await unit_of_work.publication.mark_index_job_indexed(
                    job.id,
                    content_hash=context.artifact.content_hash,
                    indexed_chunk_count=count,
                    expected_revision=job.revision,
                )
            await unit_of_work.publication.transition_publication_run(
                run.id,
                PublicationRunStatus.INDEX_VERIFIED,
                expected_revision=run.revision,
            )
            await unit_of_work.commit()

    async def _publish_vault(self, context: _Context) -> None:
        already_published = False
        try:
            await self._vault.verify(
                context.version.vault_path,
                expected_hash=context.artifact.content_hash,
            )
            already_published = True
        except Exception:
            pass
        if not already_published:
            previous_id = context.note.current_curated_version_id
            previous_hash: str | None = None
            if previous_id is not None:
                async with self._uow_factory() as unit_of_work:
                    previous = await unit_of_work.publication.get_curated_version(previous_id)
                previous_hash = previous.content_hash
            await self._vault.publish(
                context.artifact,
                context.version.vault_path,
                expected_current_version_id=previous_id,
                expected_current_hash=previous_hash,
            )
        await self._transition_run(context.run, PublicationRunStatus.VAULT_PUBLISHED)

    async def _activate(self, context: _Context) -> None:
        await self._vault.verify(
            context.version.vault_path,
            expected_hash=context.artifact.content_hash,
        )
        async with self._uow_factory() as unit_of_work:
            run = await unit_of_work.publication.get_publication_run(context.run.id)
            note = await unit_of_work.publication.get_note(context.note.id)
            version = await unit_of_work.publication.get_curated_version(context.version.id)
            job = await unit_of_work.publication.get_index_job(context.job.id)
            change = await unit_of_work.publication.get_knowledge_change(context.change.id)
            generation = await unit_of_work.publication.get_index_generation(context.generation.id)
            await unit_of_work.publication.activate_publication(
                run_id=run.id,
                job_id=job.id,
                expected_run_revision=run.revision,
                expected_note_revision=note.revision,
                expected_version_revision=version.revision,
                expected_job_revision=job.revision,
                expected_change_revision=change.revision,
                expected_generation_revision=generation.revision,
            )
            await unit_of_work.commit()

    async def _resume_failed(self, context: _Context) -> None:
        target = PublicationRunStatus.CURATING
        try:
            await self._vault.verify(
                context.version.vault_path,
                expected_hash=context.artifact.content_hash,
            )
            target = PublicationRunStatus.VAULT_PUBLISHED
        except Exception:
            if context.job.status in {IndexJobStatus.INDEXED, IndexJobStatus.ACTIVE_INDEXED}:
                target = PublicationRunStatus.INDEX_VERIFIED
            elif await self._vault.exists(self._vault.staging_path(context.artifact)):
                try:
                    await self._vault.verify(
                        self._vault.staging_path(context.artifact),
                        expected_hash=context.artifact.content_hash,
                    )
                    target = PublicationRunStatus.VAULT_STAGED
                except Exception:
                    target = PublicationRunStatus.CURATING
        await self._transition_run(context.run, target)

    async def _transition_run(
        self, run: PublicationRunRecord, target: PublicationRunStatus
    ) -> None:
        async with self._uow_factory() as unit_of_work:
            current = await unit_of_work.publication.get_publication_run(run.id)
            await unit_of_work.publication.transition_publication_run(
                current.id, target, expected_revision=current.revision
            )
            await unit_of_work.commit()

    async def _fail(self, run_id: PublicationRunId, job_id: IndexJobId, category: str) -> None:
        async with self._uow_factory() as unit_of_work:
            run = await unit_of_work.publication.get_publication_run(run_id)
            job = await unit_of_work.publication.get_index_job(job_id)
            if run.status not in {PublicationRunStatus.COMPLETED, PublicationRunStatus.FAILED}:
                await unit_of_work.publication.transition_publication_run(
                    run.id,
                    PublicationRunStatus.FAILED,
                    expected_revision=run.revision,
                    error_category=category,
                )
            if job.status is IndexJobStatus.INDEXING:
                await unit_of_work.publication.transition_index_job(
                    job.id,
                    IndexJobStatus.FAILED,
                    expected_revision=job.revision,
                    error_category=category,
                )
            await unit_of_work.commit()


def _validate_inputs(
    change: KnowledgeChangeRecord,
    generation: IndexGenerationRecord,
    records: Sequence[ClaimRecord],
) -> None:
    if change.status is not KnowledgeChangeStatus.PUBLISH_INTENT:
        raise PublicationError("knowledge change is not approved for publication")
    if generation.status not in {IndexGenerationStatus.STAGING, IndexGenerationStatus.ACTIVE}:
        raise PublicationError("index generation is not publishable")
    if not records:
        raise PublicationError("knowledge change has no governed Claims")


def _report(context: _Context) -> PublicationReport:
    return PublicationReport(
        run_id=context.run.id,
        change_id=context.change.id,
        note_id=context.note.id,
        curated_version_id=context.version.id,
        generation_id=context.generation.id,
        status=context.run.status,
        chunk_count=len(context.chunks),
        vault_path=context.version.vault_path,
    )


def _error_category(error: Exception) -> str:
    return type(error).__name__.upper()[:100]


__all__ = ["PublicationReport", "PublicationRunner"]
