"""Manual full-scan orchestration over deterministic ingestion components."""

from __future__ import annotations

from pydantic import Field

from trustworthy_kb.config import IngestionSettings
from trustworthy_kb.domain import (
    IngestionAction,
    IngestionItemId,
    IngestionRunId,
    IngestionRunRecord,
    IngestionRunStatus,
)
from trustworthy_kb.ingestion.adapters import ObsidianCliInventory
from trustworthy_kb.ingestion.errors import (
    IngestionError,
    MarkdownParseError,
    UnsupportedEncodingError,
)
from trustworthy_kb.ingestion.hashing import canonical_json_hash, vault_id_hash
from trustworthy_kb.ingestion.inventory import VaultInventory
from trustworthy_kb.ingestion.manifest import IngestionManifest, ManifestEntry, build_manifest
from trustworthy_kb.ingestion.markdown import MarkdownBlockParser
from trustworthy_kb.ingestion.paths import path_is_in_scope
from trustworthy_kb.ingestion.planner import KnownSource, plan_ingestion
from trustworthy_kb.ingestion.reader import StableMarkdownReader, decode_markdown
from trustworthy_kb.ingestion.safety import DocumentSafetyScanner
from trustworthy_kb.ingestion.service import (
    IngestionService,
    PreparedDocument,
    materialize_plan_items,
)
from trustworthy_kb.ingestion.snapshots import ContentAddressedSnapshotStore
from trustworthy_kb.ingestion.types import IngestionValue
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.persistence.errors import RecordNotFoundError
from trustworthy_kb.persistence.unit_of_work import SqliteUnitOfWorkFactory


class IngestionReport(IngestionValue):
    run_id: IngestionRunId
    status: IngestionRunStatus
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    skipped: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    failed: int = Field(ge=0)


class ManualIngestionRunner:
    """Run one deterministic, sequential full scan."""

    def __init__(
        self,
        *,
        inventory: VaultInventory,
        unit_of_work_factory: SqliteUnitOfWorkFactory,
        reader: StableMarkdownReader,
        snapshots: ContentAddressedSnapshotStore,
        parser: MarkdownBlockParser,
        safety_scanner: DocumentSafetyScanner,
        service: IngestionService,
        vault_hash: str,
        allowed_roots: tuple[str, ...] = (".",),
        excluded_roots: tuple[str, ...] = (),
    ) -> None:
        self._inventory = inventory
        self._unit_of_work_factory = unit_of_work_factory
        self._reader = reader
        self._snapshots = snapshots
        self._parser = parser
        self._safety_scanner = safety_scanner
        self._service = service
        self._vault_hash = vault_hash
        self._allowed_roots = allowed_roots
        self._excluded_roots = excluded_roots
        self._scope_hash = canonical_json_hash(
            {
                "allowed_roots": list(allowed_roots),
                "excluded_roots": list(excluded_roots),
                "rules_version": 1,
            }
        )

    async def run(self) -> IngestionReport:
        """Execute begin, plan, apply, and reconciliation phases."""

        run_id = IngestionRunId.generate()
        await self.begin_run(run_id)
        try:
            await self.plan_run(run_id)
            await self.apply_pending(run_id)
            return await self.reconcile_run(run_id)
        except Exception:
            await self._fail_planning_run(run_id)
            raise

    async def begin_run(self, run_id: IngestionRunId) -> IngestionRunRecord:
        """Create a PLANNING run, or return the same run during replay."""

        async with self._unit_of_work_factory() as unit_of_work:
            try:
                return await unit_of_work.ingestion.get_run(run_id)
            except RecordNotFoundError:
                timestamp = utc_now()
                record = IngestionRunRecord(
                    id=run_id,
                    vault_id_hash=self._vault_hash,
                    scan_scope_hash=self._scope_hash,
                    manifest_hash=canonical_json_hash([]),
                    status=IngestionRunStatus.PLANNING,
                    revision=1,
                    started_at=timestamp,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                created = await unit_of_work.ingestion.begin_run(record)
                await unit_of_work.commit()
                return created

    async def inventory_count(self) -> int:
        """Validate a complete inventory without returning paths to workflow state."""

        result = await self._inventory.inventory()
        if not result.complete:
            raise IngestionError("inventory is incomplete")
        return len(result.files)

    async def capture_inventory(self) -> IngestionManifest:
        """Capture and snapshot all inventory entries for deterministic planning."""

        inventory = await self._inventory.inventory()
        entries: list[ManifestEntry] = []
        for observation in inventory.files:
            try:
                document = await self._reader.read(observation.relative_path)
                await self._snapshots.put(document.raw_bytes, document.content_hash)
                entries.append(ManifestEntry.captured(document))
            except IngestionError as error:
                entries.append(ManifestEntry.failed(observation, _error_category(error)))
            except OSError:
                entries.append(ManifestEntry.failed(observation, "FILESYSTEM_ERROR"))
        return build_manifest(entries, complete=inventory.complete)

    async def plan_run(self, run_id: IngestionRunId) -> int:
        """Persist a frozen plan and move the run to APPLYING."""

        async with self._unit_of_work_factory() as unit_of_work:
            run = await unit_of_work.ingestion.get_run(run_id)
            if run.status is not IngestionRunStatus.PLANNING:
                return run.total_items
        manifest = await self.capture_inventory()
        known_sources = await self._load_known_sources()
        plan = plan_ingestion(manifest, known_sources)
        records = materialize_plan_items(run_id, plan)
        async with self._unit_of_work_factory() as unit_of_work:
            run = await unit_of_work.ingestion.get_run(run_id)
            if run.status is not IngestionRunStatus.PLANNING:
                return run.total_items
            planned, _ = await unit_of_work.ingestion.save_plan(
                run.id,
                plan.manifest_hash,
                records,
                expected_run_revision=run.revision,
            )
            await unit_of_work.ingestion.transition_run(
                run.id,
                IngestionRunStatus.APPLYING,
                expected_revision=planned.revision,
            )
            await unit_of_work.commit()
        return len(records)

    async def apply_pending(self, run_id: IngestionRunId) -> int:
        """Apply all pending items sequentially and return the processed count."""

        async with self._unit_of_work_factory() as unit_of_work:
            pending = await unit_of_work.ingestion.list_pending_items(run_id)
        applied = 0
        for item in pending:
            prepared: PreparedDocument | None = None
            if item.error_category is None and item.action is not IngestionAction.DELETED:
                try:
                    prepared = await self._prepare_item(item.relative_path, item.content_hash)
                except IngestionError as error:
                    await self._mark_item_failed(item.id, _error_category(error))
                    applied += 1
                    continue
                except OSError:
                    await self._mark_item_failed(item.id, "FILESYSTEM_ERROR")
                    applied += 1
                    continue
            await self._service.apply_item(item.id, prepared)
            applied += 1
        return applied

    async def reconcile_run(self, run_id: IngestionRunId) -> IngestionReport:
        """Finalize a fully applied run, or replay its terminal report."""

        async with self._unit_of_work_factory() as unit_of_work:
            run = await unit_of_work.ingestion.get_run(run_id)
            if run.status in {
                IngestionRunStatus.COMPLETED,
                IngestionRunStatus.PARTIAL_FAILED,
                IngestionRunStatus.FAILED,
                IngestionRunStatus.ABANDONED,
            }:
                return _report(run)
            summary = await unit_of_work.ingestion.summarize_run(run_id)
            if summary.pending or summary.applying:
                raise IngestionError("ingestion run still has unfinished items")
            target = (
                IngestionRunStatus.PARTIAL_FAILED
                if summary.failed or summary.quarantined
                else IngestionRunStatus.COMPLETED
            )
            completed = await unit_of_work.ingestion.transition_run(
                run.id,
                target,
                expected_revision=run.revision,
            )
            await unit_of_work.commit()
            return _report(completed)

    async def _prepare_item(
        self,
        relative_path: str,
        expected_content_hash: str | None,
    ) -> PreparedDocument:
        document = await self._reader.read(relative_path)
        if document.content_hash != expected_content_hash:
            raise IngestionError("Markdown changed after the frozen ingestion plan")
        await self._snapshots.put(document.raw_bytes, document.content_hash)
        try:
            text = decode_markdown(document.raw_bytes)
            parsed = self._parser.parse(text)
        except (UnsupportedEncodingError, MarkdownParseError) as error:
            return PreparedDocument(
                content_hash=document.content_hash,
                byte_size=len(document.raw_bytes),
                mtime_ns=document.observation.mtime_ns,
                parse_error_category=_error_category(error),
            )
        return PreparedDocument(
            content_hash=document.content_hash,
            byte_size=len(document.raw_bytes),
            mtime_ns=document.observation.mtime_ns,
            parsed=parsed,
            safety=self._safety_scanner.scan(text),
        )

    async def _load_known_sources(self) -> tuple[KnownSource, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            locations = await unit_of_work.ingestion.list_live_source_locations(self._vault_hash)
            known: list[KnownSource] = []
            for location in locations:
                source = await unit_of_work.sources.get_source(location.source_id)
                current = await unit_of_work.sources.get_current_source_version(source.id)
                latest = await unit_of_work.sources.get_latest_source_version(source.id)
                known.append(
                    KnownSource(
                        source_id=source.id,
                        relative_path=location.relative_path,
                        path_key=location.path_key,
                        file_key=location.file_key,
                        current_version_id=(None if current is None else current.id),
                        current_content_hash=(None if current is None else current.content_hash),
                        latest_version_id=(None if latest is None else latest.id),
                        latest_content_hash=(None if latest is None else latest.content_hash),
                        latest_status=(None if latest is None else latest.status),
                        eligible_for_deletion=path_is_in_scope(
                            location.relative_path,
                            allowed_roots=self._allowed_roots,
                            excluded_roots=self._excluded_roots,
                        ),
                    )
                )
            return tuple(known)

    async def _mark_item_failed(
        self,
        item_id: IngestionItemId,
        error_category: str,
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            item = await unit_of_work.ingestion.get_item(item_id)
            applying = await unit_of_work.ingestion.start_item(
                item.id, expected_revision=item.revision
            )
            await unit_of_work.ingestion.fail_item(
                item.id,
                error_category=error_category,
                expected_revision=applying.revision,
            )
            await unit_of_work.commit()

    async def _fail_planning_run(self, run_id: IngestionRunId) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            run = await unit_of_work.ingestion.get_run(run_id)
            if run.status is not IngestionRunStatus.PLANNING:
                return
            await unit_of_work.ingestion.transition_run(
                run.id,
                IngestionRunStatus.FAILED,
                expected_revision=run.revision,
                error_category="PLANNING_FAILED",
            )
            await unit_of_work.commit()


def build_manual_runner(
    settings: IngestionSettings,
    unit_of_work_factory: SqliteUnitOfWorkFactory,
) -> ManualIngestionRunner:
    """Build the production manual runner from validated settings."""

    vault_hash = vault_id_hash(settings.vault_id_value)
    inventory = ObsidianCliInventory(
        executable=settings.obsidian_executable,
        vault_id=settings.vault_id_value,
        vault_root=settings.vault_path_value,
        allowed_roots=settings.allowed_roots,
        excluded_roots=settings.excluded_roots,
        timeout_seconds=settings.cli_timeout_seconds,
        output_limit_bytes=settings.cli_output_limit_bytes,
    )
    reader = StableMarkdownReader(
        settings.vault_path_value,
        max_bytes=settings.max_markdown_bytes,
        attempts=settings.stable_read_attempts,
        interval_ms=settings.stable_read_interval_ms,
    )
    service = IngestionService(unit_of_work_factory, vault_id_hash=vault_hash)
    return ManualIngestionRunner(
        inventory=inventory,
        unit_of_work_factory=unit_of_work_factory,
        reader=reader,
        snapshots=ContentAddressedSnapshotStore(settings.snapshot_root_value),
        parser=MarkdownBlockParser(),
        safety_scanner=DocumentSafetyScanner(),
        service=service,
        vault_hash=vault_hash,
        allowed_roots=settings.allowed_roots,
        excluded_roots=settings.excluded_roots,
    )


def _error_category(error: BaseException) -> str:
    mapping = {
        "DocumentTooLargeError": "DOCUMENT_TOO_LARGE",
        "MarkdownParseError": "MARKDOWN_PARSE_FAILED",
        "SnapshotIntegrityError": "SNAPSHOT_INTEGRITY",
        "UnsupportedEncodingError": "UNSUPPORTED_ENCODING",
        "UnstableFileError": "UNSTABLE_FILE",
        "VaultPathPolicyError": "VAULT_PATH_POLICY",
    }
    return mapping.get(type(error).__name__, "INGESTION_ERROR")


def _report(run: IngestionRunRecord) -> IngestionReport:
    return IngestionReport(
        run_id=run.id,
        status=run.status,
        total=run.total_items,
        succeeded=run.succeeded_items,
        skipped=run.skipped_items,
        quarantined=run.quarantined_items,
        failed=run.failed_items,
    )


__all__ = ["IngestionReport", "ManualIngestionRunner", "build_manual_runner"]
