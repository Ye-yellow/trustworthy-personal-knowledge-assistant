from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    ActorType,
    EntityType,
    IdempotencyStatus,
    InvariantViolationError,
    ModelRunId,
    ModelRunPurpose,
    ModelRunRecord,
    ModelRunStatus,
    OperationLogId,
    OperationLogRecord,
    Sensitivity,
    SourceId,
    SourceRecord,
    SourceType,
    TrustTier,
    operation_log_entry_hash,
)
from trustworthy_kb.persistence import Base, create_database_engine, create_session_factory
from trustworthy_kb.persistence.audit_repository import AuditRepository
from trustworthy_kb.persistence.errors import (
    ConcurrentModificationError,
    IdempotencyConflictError,
    OperationInProgressError,
)
from trustworthy_kb.persistence.source_repository import SourceRepository


def now() -> datetime:
    return datetime.now(UTC)


def model_run_record() -> ModelRunRecord:
    return ModelRunRecord(
        id=ModelRunId.generate(),
        purpose=ModelRunPurpose.CLAIM_EXTRACTION,
        provider="sub2api",
        model="gpt-test",
        prompt_version="v1",
        status=ModelRunStatus.STARTED,
        input_hash="a" * 64,
        started_at=now(),
        revision=1,
    )


def operation_record(
    source_id: SourceId,
    *,
    step: int,
    previous_hash: str | None,
    entry_hash: str | None = None,
) -> OperationLogRecord:
    created_at = now()
    values = {
        "operation_id": "audit-op",
        "step_number": step,
        "actor_type": ActorType.SYSTEM,
        "actor_id": None,
        "action": "SOURCE_STATE",
        "target_type": EntityType.SOURCE,
        "target_id": source_id,
        "before_json": {"revision": step},
        "after_json": {"revision": step + 1},
        "previous_entry_hash": previous_hash,
        "created_at": created_at,
    }
    return OperationLogRecord(
        id=OperationLogId.generate(),
        entry_hash=entry_hash or operation_log_entry_hash(**values),
        **values,
    )


async def seed_source(repository: SourceRepository) -> SourceId:
    timestamp = now()
    source = SourceRecord(
        id=SourceId.generate(),
        source_type=SourceType.USER_INPUT,
        canonical_uri="user://audit-target",
        owner="test",
        trust_tier=TrustTier.T0,
        sensitivity=Sensitivity.PRIVATE,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )
    await repository.add_source(source)
    return source.id


@pytest.mark.asyncio
async def test_audit_repository_tracks_model_run_and_hash_chain(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'audit.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    repository = AuditRepository(session)
    try:
        source_id = await seed_source(SourceRepository(session))
        model_run = model_run_record()
        assert await repository.start_model_run(model_run) == model_run
        finished = await repository.finish_model_run(
            model_run.id,
            ModelRunStatus.SUCCEEDED,
            expected_revision=1,
            output_hash="b" * 64,
            input_tokens=10,
            output_tokens=5,
            latency_ms=20,
            completed_at=now(),
            request_id="request-1",
        )
        assert finished.total_tokens == 15
        assert finished.status is ModelRunStatus.SUCCEEDED

        first = operation_record(source_id, step=0, previous_hash=None)
        second = operation_record(source_id, step=1, previous_hash=first.entry_hash)
        assert await repository.append_operation_log(first) == first
        assert await repository.append_operation_log(second) == second

        with pytest.raises(InvariantViolationError):
            await repository.append_operation_log(
                operation_record(source_id, step=3, previous_hash=second.entry_hash)
            )
        with pytest.raises(InvariantViolationError):
            await repository.append_operation_log(
                operation_record(
                    source_id,
                    step=2,
                    previous_hash=second.entry_hash,
                    entry_hash="0" * 64,
                )
            )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_repository_model_run_uses_transition_and_revision_guards(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'model-run-errors.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    repository = AuditRepository(session)
    try:
        model_run = model_run_record()
        await repository.start_model_run(model_run)
        with pytest.raises(ConcurrentModificationError):
            await repository.finish_model_run(
                model_run.id,
                ModelRunStatus.FAILED,
                expected_revision=99,
                input_tokens=0,
                output_tokens=0,
                latency_ms=None,
                completed_at=now(),
                error_category="PROVIDER_ERROR",
            )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_idempotency_acquire_takeover_completion_and_replay(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'idempotency.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    repository = AuditRepository(session)
    started = datetime(2026, 7, 28, tzinfo=UTC)
    try:
        acquired = await repository.acquire_idempotency_key(
            scope="source.capture",
            idempotency_key="capture-1",
            request_hash="c" * 64,
            lease_owner="worker-1",
            lease_duration=timedelta(minutes=1),
            now=started,
        )
        assert acquired.status is IdempotencyStatus.IN_PROGRESS
        assert acquired.attempt == 1

        with pytest.raises(OperationInProgressError):
            await repository.acquire_idempotency_key(
                scope="source.capture",
                idempotency_key="capture-1",
                request_hash="c" * 64,
                lease_owner="worker-2",
                lease_duration=timedelta(minutes=1),
                now=started + timedelta(seconds=30),
            )
        with pytest.raises(IdempotencyConflictError):
            await repository.acquire_idempotency_key(
                scope="source.capture",
                idempotency_key="capture-1",
                request_hash="d" * 64,
                lease_owner="worker-2",
                lease_duration=timedelta(minutes=1),
                now=started + timedelta(seconds=30),
            )

        taken_over = await repository.acquire_idempotency_key(
            scope="source.capture",
            idempotency_key="capture-1",
            request_hash="c" * 64,
            lease_owner="worker-2",
            lease_duration=timedelta(minutes=1),
            now=started + timedelta(minutes=2),
        )
        assert taken_over.attempt == 2
        assert taken_over.revision == 2

        result_id = await seed_source(SourceRepository(session))
        completed = await repository.complete_idempotent_operation(
            acquired.id,
            result_type=EntityType.SOURCE,
            result_id=result_id,
            expected_revision=taken_over.revision,
        )
        assert completed.status is IdempotencyStatus.SUCCEEDED
        assert completed.result_id == result_id

        replayed = await repository.acquire_idempotency_key(
            scope="source.capture",
            idempotency_key="capture-1",
            request_hash="c" * 64,
            lease_owner="worker-3",
            lease_duration=timedelta(minutes=1),
            now=started + timedelta(days=1),
        )
        assert replayed == completed
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_idempotency_requires_explicit_reconciliation(tmp_path: Path) -> None:
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{(tmp_path / 'unknown.db').as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    repository = AuditRepository(session)
    started = datetime(2026, 7, 28, tzinfo=UTC)
    try:
        acquired = await repository.acquire_idempotency_key(
            scope="publish",
            idempotency_key="publish-1",
            request_hash="e" * 64,
            lease_owner="worker",
            lease_duration=timedelta(minutes=1),
            now=started,
        )
        unknown = await repository.mark_idempotent_operation_unknown(
            acquired.id,
            expected_revision=1,
            error_category="EXTERNAL_STATE_UNCONFIRMED",
        )
        with pytest.raises(OperationInProgressError, match="reconciliation"):
            await repository.acquire_idempotency_key(
                scope="publish",
                idempotency_key="publish-1",
                request_hash="e" * 64,
                lease_owner="worker-2",
                lease_duration=timedelta(minutes=1),
                now=started + timedelta(days=1),
            )
        with pytest.raises(InvariantViolationError):
            await repository.fail_idempotent_operation(
                acquired.id,
                expected_revision=unknown.revision,
                error_category="NOT_FOUND",
            )
        reconciled = await repository.fail_idempotent_operation(
            acquired.id,
            expected_revision=unknown.revision,
            error_category="NOT_FOUND",
            reconcile_unknown=True,
        )
        assert reconciled.status is IdempotencyStatus.FAILED
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
