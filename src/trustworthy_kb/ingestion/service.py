"""Idempotent application of one prepared ingestion plan item."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import Field, model_validator

from trustworthy_kb.domain import (
    ActorType,
    ChangeType,
    ContentBlockId,
    ContentBlockRecord,
    EntityType,
    IdempotencyRecordId,
    IdempotencyStatus,
    IngestionAction,
    IngestionItemId,
    IngestionItemRecord,
    IngestionItemStatus,
    IngestionRunId,
    KnowledgeChangeId,
    KnowledgeChangeRecord,
    KnowledgeChangeStatus,
    OperationLogId,
    OperationLogRecord,
    Sensitivity,
    SourceId,
    SourceLocationRecord,
    SourceRecord,
    SourceType,
    SourceVersionId,
    SourceVersionRecord,
    SourceVersionStatus,
    TrustTier,
    operation_log_entry_hash,
)
from trustworthy_kb.ingestion.diff import StructuralDiff, structural_diff
from trustworthy_kb.ingestion.hashing import (
    canonical_json_hash,
    canonical_source_uri,
    sha256_text,
)
from trustworthy_kb.ingestion.planner import IngestionPlan
from trustworthy_kb.ingestion.safety import SafetyReport
from trustworthy_kb.ingestion.types import IngestionValue, ParsedBlock, ParsedDocument
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.persistence.unit_of_work import SqliteUnitOfWork, SqliteUnitOfWorkFactory

_TERMINAL_ITEMS = frozenset(
    {
        IngestionItemStatus.SUCCEEDED,
        IngestionItemStatus.SKIPPED,
        IngestionItemStatus.QUARANTINED,
        IngestionItemStatus.FAILED,
    }
)


class PreparedDocument(IngestionValue):
    """Transient prepared data retained in memory after snapshot and parsing."""

    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)
    mtime_ns: int = Field(ge=0)
    parsed: ParsedDocument | None = None
    safety: SafetyReport | None = None
    parse_error_category: str | None = None

    @model_validator(mode="after")
    def _validate_result(self) -> PreparedDocument:
        if self.parsed is None:
            if self.parse_error_category is None or self.safety is not None:
                raise ValueError("failed preparation requires only an error category")
        elif self.parse_error_category is not None or self.safety is None:
            raise ValueError("parsed preparation requires a safety report")
        return self


def materialize_plan_items(
    run_id: IngestionRunId,
    plan: IngestionPlan,
    *,
    created_at: datetime | None = None,
) -> tuple[IngestionItemRecord, ...]:
    """Assign typed IDs and stable per-attempt operation IDs to a frozen plan."""

    timestamp = created_at or utc_now()
    records: list[IngestionItemRecord] = []
    for plan_item in plan.items:
        item_id = IngestionItemId.generate()
        operation_id = f"ingop_{sha256_text(f'{run_id}:{item_id}:1')}"
        records.append(
            IngestionItemRecord(
                id=item_id,
                run_id=run_id,
                source_id=plan_item.source_id,
                action=plan_item.action,
                relative_path=plan_item.relative_path,
                path_key=plan_item.path_key,
                file_key=plan_item.file_key,
                content_hash=plan_item.content_hash,
                base_version_id=plan_item.base_version_id,
                status=IngestionItemStatus.PENDING,
                operation_id=operation_id,
                error_category=plan_item.error_category,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
    return tuple(records)


class IngestionService:
    """Apply one item atomically through the L1 Unit of Work."""

    def __init__(
        self,
        unit_of_work_factory: SqliteUnitOfWorkFactory,
        *,
        vault_id_hash: str,
        owner: str = "local-vault",
        lease_owner: str = "ingestion-worker",
        lease_duration: timedelta = timedelta(minutes=5),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._vault_id_hash = vault_id_hash
        self._owner = owner
        self._lease_owner = lease_owner
        self._lease_duration = lease_duration

    async def apply_item(
        self,
        item_id: IngestionItemId,
        prepared: PreparedDocument | None = None,
    ) -> IngestionItemRecord:
        """Apply or safely replay one ingestion item."""

        async with self._unit_of_work_factory() as unit_of_work:
            item = await unit_of_work.ingestion.get_item(item_id)
            if item.status in _TERMINAL_ITEMS:
                return item
            applying = await unit_of_work.ingestion.start_item(
                item.id, expected_revision=item.revision
            )
            idempotency = await unit_of_work.audit.acquire_idempotency_key(
                scope="ingestion.file",
                idempotency_key=item.operation_id,
                request_hash=_request_hash(item),
                lease_owner=self._lease_owner,
                lease_duration=self._lease_duration,
            )
            if idempotency.status is IdempotencyStatus.SUCCEEDED:
                raise RuntimeError("idempotency state is inconsistent with ingestion item")
            result = await self._dispatch(unit_of_work, applying, prepared, idempotency.id)
            await unit_of_work.commit()
            return result

    async def _dispatch(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        prepared: PreparedDocument | None,
        idempotency_id: IdempotencyRecordId,
    ) -> IngestionItemRecord:
        if item.error_category is not None:
            failed = await unit_of_work.ingestion.fail_item(
                item.id,
                error_category=item.error_category,
                expected_revision=item.revision,
            )
            await unit_of_work.audit.fail_idempotent_operation(
                idempotency_id,
                expected_revision=1,
                error_category=item.error_category,
            )
            return failed
        if item.action is IngestionAction.CREATED:
            if item.source_id is not None:
                return await self._apply_existing_content(
                    unit_of_work,
                    item,
                    _require_prepared(item, prepared),
                    idempotency_id,
                )
            return await self._apply_created(
                unit_of_work, item, _require_prepared(item, prepared), idempotency_id
            )
        if item.action in {IngestionAction.UPDATED, IngestionAction.MOVED}:
            return await self._apply_existing_content(
                unit_of_work,
                item,
                _require_prepared(item, prepared),
                idempotency_id,
            )
        if item.action is IngestionAction.DELETED:
            return await self._apply_deleted(unit_of_work, item, idempotency_id)
        return await self._apply_unchanged(unit_of_work, item, prepared, idempotency_id)

    async def _apply_created(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        prepared: PreparedDocument,
        idempotency_id: IdempotencyRecordId,
    ) -> IngestionItemRecord:
        timestamp = utc_now()
        source = SourceRecord(
            id=SourceId.generate(),
            source_type=SourceType.OBSIDIAN_MARKDOWN,
            canonical_uri=canonical_source_uri(self._vault_id_hash, item.relative_path),
            owner=self._owner,
            trust_tier=TrustTier.T0,
            sensitivity=Sensitivity.PRIVATE,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        await unit_of_work.sources.add_source(source)
        await unit_of_work.ingestion.record_source_location(
            _source_location(
                source.id,
                item,
                prepared,
                timestamp,
                vault_id_hash=self._vault_id_hash,
            )
        )
        version = await self._append_captured_version(
            unit_of_work,
            source.id,
            item,
            prepared,
            version_number=1,
            timestamp=timestamp,
        )
        return await self._finish_prepared_version(
            unit_of_work,
            item,
            source,
            version,
            prepared,
            idempotency_id,
            base_version=None,
            change_type=ChangeType.CREATED,
        )

    async def _apply_existing_content(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        prepared: PreparedDocument,
        idempotency_id: IdempotencyRecordId,
    ) -> IngestionItemRecord:
        source_id = _require_source_id(item)
        source = await unit_of_work.sources.get_source(source_id)
        current = await unit_of_work.sources.get_current_source_version(source_id)
        latest = await unit_of_work.sources.get_latest_source_version(source_id)
        existing = await unit_of_work.sources.find_source_version_by_hash(
            source_id, prepared.content_hash
        )
        timestamp = utc_now()
        previous_path_key: str | None = None
        if item.action is IngestionAction.MOVED:
            location = await unit_of_work.ingestion.get_source_location(source_id)
            previous_path_key = location.path_key
            source = await unit_of_work.sources.move_source(
                source_id,
                canonical_source_uri(self._vault_id_hash, item.relative_path),
                expected_revision=source.revision,
            )
            await unit_of_work.ingestion.move_source_location(
                source_id,
                relative_path=item.relative_path,
                path_key=item.path_key,
                file_key=item.file_key,
                last_seen_run_id=item.run_id,
                observed_size=prepared.byte_size,
                observed_mtime_ns=prepared.mtime_ns,
                expected_revision=location.revision,
            )
        else:
            location = await unit_of_work.ingestion.get_source_location(source_id)
            await unit_of_work.ingestion.touch_source_location(
                source_id,
                file_key=item.file_key,
                last_seen_run_id=item.run_id,
                observed_size=prepared.byte_size,
                observed_mtime_ns=prepared.mtime_ns,
                expected_revision=location.revision,
            )

        if existing is not None and existing.status is not SourceVersionStatus.PARSE_FAILED:
            if existing.status is SourceVersionStatus.QUARANTINED:
                return await self._finish_preserved_quarantine(
                    unit_of_work, item, source, existing, idempotency_id
                )
            if existing.status is not SourceVersionStatus.READY:
                raise RuntimeError("existing source version is not reusable")
            if current is not None and existing.id == current.id:
                if item.action is not IngestionAction.MOVED:
                    raise RuntimeError("updated item does not change the current version")
                return await self._finish_move_without_version(
                    unit_of_work,
                    item,
                    source,
                    existing,
                    idempotency_id,
                    previous_path_key=previous_path_key,
                )
            return await self._finish_ready_reactivation(
                unit_of_work,
                item,
                source,
                existing,
                current,
                idempotency_id,
                previous_path_key=previous_path_key,
            )
        if existing is None:
            version = await self._append_captured_version(
                unit_of_work,
                source_id,
                item,
                prepared,
                version_number=1 if latest is None else latest.version_number + 1,
                timestamp=timestamp,
            )
        else:
            version = existing
            if prepared.parsed is not None:
                await unit_of_work.sources.add_content_blocks(
                    _content_blocks(version.id, prepared.parsed, timestamp)
                )
        change_type = (
            ChangeType.MOVED
            if item.action is IngestionAction.MOVED
            else ChangeType.UPDATED
            if current is not None
            else ChangeType.CREATED
        )
        return await self._finish_prepared_version(
            unit_of_work,
            item,
            source,
            version,
            prepared,
            idempotency_id,
            base_version=current,
            change_type=change_type,
            previous_path_key=previous_path_key,
        )

    async def _finish_prepared_version(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        source: SourceRecord,
        version: SourceVersionRecord,
        prepared: PreparedDocument,
        idempotency_id: IdempotencyRecordId,
        *,
        base_version: SourceVersionRecord | None,
        change_type: ChangeType,
        previous_path_key: str | None = None,
    ) -> IngestionItemRecord:
        if prepared.parse_error_category is not None:
            failed_version = (
                version
                if version.status is SourceVersionStatus.PARSE_FAILED
                else await unit_of_work.sources.transition_source_version(
                    version.id,
                    SourceVersionStatus.PARSE_FAILED,
                    expected_revision=version.revision,
                )
            )
            failed_item = await unit_of_work.ingestion.fail_item(
                item.id,
                error_category=prepared.parse_error_category,
                expected_revision=item.revision,
                source_id=source.id,
                result_version_id=failed_version.id,
            )
            await self._append_log(
                unit_of_work,
                item,
                EntityType.SOURCE_VERSION,
                failed_version.id,
                before={"status": version.status.value},
                after={"status": failed_version.status.value, "content_hash": version.content_hash},
            )
            await unit_of_work.audit.fail_idempotent_operation(
                idempotency_id,
                expected_revision=1,
                error_category=prepared.parse_error_category,
            )
            return failed_item

        parsed_version = await unit_of_work.sources.transition_source_version(
            version.id,
            SourceVersionStatus.PARSED,
            expected_revision=version.revision,
        )
        if prepared.safety is not None and prepared.safety.requires_quarantine:
            quarantined_version = await unit_of_work.sources.transition_source_version(
                version.id,
                SourceVersionStatus.QUARANTINED,
                expected_revision=parsed_version.revision,
            )
            quarantined_item = await unit_of_work.ingestion.complete_item(
                item.id,
                IngestionItemStatus.QUARANTINED,
                expected_revision=item.revision,
                source_id=source.id,
                result_version_id=quarantined_version.id,
                safety_signals=prepared.safety.category_counts(),
            )
            await self._append_log(
                unit_of_work,
                item,
                EntityType.SOURCE_VERSION,
                quarantined_version.id,
                before={"status": version.status.value},
                after={"status": quarantined_version.status.value},
            )
            await unit_of_work.audit.complete_idempotent_operation(
                idempotency_id,
                result_type=EntityType.SOURCE_VERSION,
                result_id=quarantined_version.id,
                expected_revision=1,
            )
            return quarantined_item

        ready = await unit_of_work.sources.transition_source_version(
            version.id,
            SourceVersionStatus.READY,
            expected_revision=parsed_version.revision,
        )
        active_source = await unit_of_work.sources.activate_source_version(
            source.id,
            ready.id,
            expected_revision=source.revision,
        )
        before_blocks = (
            ()
            if base_version is None
            else _parsed_refs(await unit_of_work.sources.list_content_blocks(base_version.id))
        )
        assert prepared.parsed is not None
        diff = structural_diff(before_blocks, prepared.parsed.blocks)
        await unit_of_work.publication.add_knowledge_change(
            _knowledge_change(
                item,
                active_source.id,
                base_version,
                ready,
                change_type,
                diff,
                previous_path_key=previous_path_key,
            )
        )
        completed_item = await unit_of_work.ingestion.complete_item(
            item.id,
            IngestionItemStatus.SUCCEEDED,
            expected_revision=item.revision,
            source_id=active_source.id,
            result_version_id=ready.id,
            safety_signals=({} if prepared.safety is None else prepared.safety.category_counts()),
        )
        await self._append_log(
            unit_of_work,
            item,
            EntityType.SOURCE_VERSION,
            ready.id,
            before={"base_version_id": None if base_version is None else str(base_version.id)},
            after={"content_hash": ready.content_hash, "status": ready.status.value},
        )
        await unit_of_work.audit.complete_idempotent_operation(
            idempotency_id,
            result_type=EntityType.SOURCE_VERSION,
            result_id=ready.id,
            expected_revision=1,
        )
        return completed_item

    async def _finish_move_without_version(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        source: SourceRecord,
        target: SourceVersionRecord,
        idempotency_id: IdempotencyRecordId,
        *,
        previous_path_key: str | None,
    ) -> IngestionItemRecord:
        diff_value = {
            "counts": {"added": 0, "modified": 0, "moved": 0, "removed": 0},
            "path_move": {
                "from_path_key": previous_path_key,
                "to_path_key": item.path_key,
            },
        }
        await unit_of_work.publication.add_knowledge_change(
            KnowledgeChangeRecord(
                id=KnowledgeChangeId.generate(),
                source_id=source.id,
                base_version_id=target.id,
                target_version_id=target.id,
                change_type=ChangeType.MOVED,
                diff_hash=canonical_json_hash(diff_value),
                diff_summary_json=diff_value,
                status=KnowledgeChangeStatus.RECEIVED,
                operation_id=item.operation_id,
                revision=1,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        completed = await unit_of_work.ingestion.complete_item(
            item.id,
            IngestionItemStatus.SUCCEEDED,
            expected_revision=item.revision,
            source_id=source.id,
            result_version_id=target.id,
        )
        await self._append_log(
            unit_of_work,
            item,
            EntityType.SOURCE,
            source.id,
            before={"path_key": previous_path_key},
            after={"path_key": item.path_key},
        )
        await unit_of_work.audit.complete_idempotent_operation(
            idempotency_id,
            result_type=EntityType.SOURCE,
            result_id=source.id,
            expected_revision=1,
        )
        return completed

    async def _finish_ready_reactivation(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        source: SourceRecord,
        target: SourceVersionRecord,
        current: SourceVersionRecord | None,
        idempotency_id: IdempotencyRecordId,
        *,
        previous_path_key: str | None,
    ) -> IngestionItemRecord:
        active_source = await unit_of_work.sources.activate_source_version(
            source.id,
            target.id,
            expected_revision=source.revision,
        )
        old_blocks = (
            ()
            if current is None
            else _parsed_refs(await unit_of_work.sources.list_content_blocks(current.id))
        )
        target_blocks = _parsed_refs(await unit_of_work.sources.list_content_blocks(target.id))
        change_type = (
            ChangeType.MOVED
            if item.action is IngestionAction.MOVED
            else ChangeType.UPDATED
            if current is not None
            else ChangeType.CREATED
        )
        await unit_of_work.publication.add_knowledge_change(
            _knowledge_change(
                item,
                active_source.id,
                current,
                target,
                change_type,
                structural_diff(old_blocks, target_blocks),
                previous_path_key=previous_path_key,
            )
        )
        completed = await unit_of_work.ingestion.complete_item(
            item.id,
            IngestionItemStatus.SUCCEEDED,
            expected_revision=item.revision,
            source_id=active_source.id,
            result_version_id=target.id,
        )
        await self._append_log(
            unit_of_work,
            item,
            EntityType.SOURCE_VERSION,
            target.id,
            before={"base_version_id": None if current is None else str(current.id)},
            after={"content_hash": target.content_hash, "status": target.status.value},
        )
        await unit_of_work.audit.complete_idempotent_operation(
            idempotency_id,
            result_type=EntityType.SOURCE_VERSION,
            result_id=target.id,
            expected_revision=1,
        )
        return completed

    async def _finish_preserved_quarantine(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        source: SourceRecord,
        target: SourceVersionRecord,
        idempotency_id: IdempotencyRecordId,
    ) -> IngestionItemRecord:
        completed = await unit_of_work.ingestion.complete_item(
            item.id,
            IngestionItemStatus.QUARANTINED,
            expected_revision=item.revision,
            source_id=source.id,
            result_version_id=target.id,
        )
        await unit_of_work.audit.complete_idempotent_operation(
            idempotency_id,
            result_type=EntityType.SOURCE_VERSION,
            result_id=target.id,
            expected_revision=1,
        )
        return completed

    async def _apply_deleted(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        idempotency_id: IdempotencyRecordId,
    ) -> IngestionItemRecord:
        source_id = _require_source_id(item)
        source = await unit_of_work.sources.get_source(source_id)
        target = await unit_of_work.sources.get_current_source_version(source_id)
        if target is None:
            target = await unit_of_work.sources.get_latest_source_version(source_id)
        if target is None:
            raise RuntimeError("source deletion requires a captured version")
        location = await unit_of_work.ingestion.get_source_location(source_id)
        deleted_source = await unit_of_work.sources.mark_source_deleted(
            source_id, expected_revision=source.revision
        )
        await unit_of_work.ingestion.mark_source_location_deleted(
            source_id, expected_revision=location.revision
        )
        deleted_version = await unit_of_work.sources.transition_source_version(
            target.id,
            SourceVersionStatus.DELETED,
            expected_revision=target.revision,
        )
        old_blocks = _parsed_refs(await unit_of_work.sources.list_content_blocks(target.id))
        diff = structural_diff(old_blocks, ())
        await unit_of_work.publication.add_knowledge_change(
            _knowledge_change(
                item,
                deleted_source.id,
                deleted_version,
                deleted_version,
                ChangeType.DELETED,
                diff,
            )
        )
        completed = await unit_of_work.ingestion.complete_item(
            item.id,
            IngestionItemStatus.SUCCEEDED,
            expected_revision=item.revision,
            source_id=source_id,
            result_version_id=deleted_version.id,
        )
        await self._append_log(
            unit_of_work,
            item,
            EntityType.SOURCE,
            source_id,
            before={"deleted": False},
            after={"deleted": True},
        )
        await unit_of_work.audit.complete_idempotent_operation(
            idempotency_id,
            result_type=EntityType.SOURCE,
            result_id=source_id,
            expected_revision=1,
        )
        return completed

    async def _apply_unchanged(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        prepared: PreparedDocument | None,
        idempotency_id: IdempotencyRecordId,
    ) -> IngestionItemRecord:
        source_id = _require_source_id(item)
        latest = await unit_of_work.sources.get_latest_source_version(source_id)
        if latest is None:
            raise RuntimeError("unchanged source requires a captured version")
        if prepared is not None:
            location = await unit_of_work.ingestion.get_source_location(source_id)
            await unit_of_work.ingestion.touch_source_location(
                source_id,
                file_key=item.file_key,
                last_seen_run_id=item.run_id,
                observed_size=prepared.byte_size,
                observed_mtime_ns=prepared.mtime_ns,
                expected_revision=location.revision,
            )
        target_status = (
            IngestionItemStatus.QUARANTINED
            if latest.status is SourceVersionStatus.QUARANTINED
            else IngestionItemStatus.SKIPPED
        )
        completed = await unit_of_work.ingestion.complete_item(
            item.id,
            target_status,
            expected_revision=item.revision,
            source_id=source_id,
            result_version_id=latest.id,
        )
        await unit_of_work.audit.complete_idempotent_operation(
            idempotency_id,
            result_type=EntityType.SOURCE,
            result_id=source_id,
            expected_revision=1,
        )
        return completed

    async def _append_captured_version(
        self,
        unit_of_work: SqliteUnitOfWork,
        source_id: SourceId,
        item: IngestionItemRecord,
        prepared: PreparedDocument,
        *,
        version_number: int,
        timestamp: datetime,
    ) -> SourceVersionRecord:
        version = SourceVersionRecord(
            id=SourceVersionId.generate(),
            source_id=source_id,
            version_number=version_number,
            content_hash=prepared.content_hash,
            byte_size=prepared.byte_size,
            media_type="text/markdown",
            captured_at=timestamp,
            source_modified_at=datetime.fromtimestamp(prepared.mtime_ns / 1_000_000_000, UTC),
            original_path=item.relative_path,
            status=SourceVersionStatus.CAPTURED,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        created = await unit_of_work.sources.append_source_version(version)
        if prepared.parsed is not None:
            await unit_of_work.sources.add_content_blocks(
                _content_blocks(created.id, prepared.parsed, timestamp)
            )
        return created

    async def _append_log(
        self,
        unit_of_work: SqliteUnitOfWork,
        item: IngestionItemRecord,
        target_type: EntityType,
        target_id: SourceId | SourceVersionId,
        *,
        before: dict[str, object],
        after: dict[str, object],
    ) -> None:
        timestamp = utc_now()
        entry_hash = operation_log_entry_hash(
            operation_id=item.operation_id,
            step_number=0,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            action=f"INGESTION_{item.action.value}",
            target_type=target_type,
            target_id=target_id,
            before_json=before,
            after_json=after,
            previous_entry_hash=None,
            created_at=timestamp,
        )
        await unit_of_work.audit.append_operation_log(
            OperationLogRecord(
                id=OperationLogId.generate(),
                operation_id=item.operation_id,
                step_number=0,
                actor_type=ActorType.SYSTEM,
                action=f"INGESTION_{item.action.value}",
                target_type=target_type,
                target_id=target_id,
                before_json=before,
                after_json=after,
                entry_hash=entry_hash,
                created_at=timestamp,
            )
        )


def _request_hash(item: IngestionItemRecord) -> str:
    return canonical_json_hash(
        {
            "action": item.action.value,
            "attempt": item.attempt,
            "content_hash": item.content_hash,
            "item_id": str(item.id),
            "path_key": item.path_key,
            "run_id": str(item.run_id),
        }
    )


def _require_source_id(item: IngestionItemRecord) -> SourceId:
    if item.source_id is None:
        raise RuntimeError("ingestion item requires a source identity")
    return item.source_id


def _require_prepared(
    item: IngestionItemRecord,
    prepared: PreparedDocument | None,
) -> PreparedDocument:
    if prepared is None or prepared.content_hash != item.content_hash:
        raise RuntimeError("prepared document does not match ingestion item")
    return prepared


def _source_location(
    source_id: SourceId,
    item: IngestionItemRecord,
    prepared: PreparedDocument,
    timestamp: datetime,
    *,
    vault_id_hash: str,
) -> SourceLocationRecord:
    return SourceLocationRecord(
        source_id=source_id,
        vault_id_hash=vault_id_hash,
        relative_path=item.relative_path,
        path_key=item.path_key,
        file_key=item.file_key,
        last_seen_run_id=item.run_id,
        observed_size=prepared.byte_size,
        observed_mtime_ns=prepared.mtime_ns,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _content_blocks(
    version_id: SourceVersionId,
    parsed: ParsedDocument,
    timestamp: datetime,
) -> tuple[ContentBlockRecord, ...]:
    return tuple(
        ContentBlockRecord(
            id=ContentBlockId.generate(),
            source_version_id=version_id,
            ordinal=block.ordinal,
            block_type=block.block_type,
            anchor=block.anchor,
            text_hash=block.text_hash,
            character_count=block.character_count,
            created_at=timestamp,
        )
        for block in parsed.blocks
    )


def _parsed_refs(records: tuple[ContentBlockRecord, ...]) -> tuple[ParsedBlock, ...]:
    return tuple(
        ParsedBlock(
            ordinal=record.ordinal,
            block_type=record.block_type,
            anchor=record.anchor,
            text_hash=record.text_hash,
            character_count=record.character_count,
            text="",
        )
        for record in records
    )


def _knowledge_change(
    item: IngestionItemRecord,
    source_id: SourceId,
    base_version: SourceVersionRecord | None,
    target_version: SourceVersionRecord,
    change_type: ChangeType,
    diff: StructuralDiff,
    *,
    previous_path_key: str | None = None,
) -> KnowledgeChangeRecord:
    timestamp = utc_now()
    summary = diff.as_json()
    if change_type is ChangeType.MOVED:
        summary["path_move"] = {
            "from_path_key": previous_path_key,
            "to_path_key": item.path_key,
        }
    return KnowledgeChangeRecord(
        id=KnowledgeChangeId.generate(),
        source_id=source_id,
        base_version_id=None if base_version is None else base_version.id,
        target_version_id=target_version.id,
        change_type=change_type,
        diff_hash=canonical_json_hash(summary),
        diff_summary_json=summary,
        status=KnowledgeChangeStatus.RECEIVED,
        operation_id=item.operation_id,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


__all__ = ["IngestionService", "PreparedDocument", "materialize_plan_items"]
