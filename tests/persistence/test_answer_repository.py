from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trustworthy_kb.config import DatabaseSettings
from trustworthy_kb.domain import (
    AnswerRunId,
    AnswerRunRecord,
    AnswerRunStatus,
    AnswerScope,
    IndexGenerationId,
    IndexGenerationRecord,
    IndexGenerationStatus,
)
from trustworthy_kb.persistence import Base, create_database_engine, create_session_factory
from trustworthy_kb.persistence.answer_repository import AnswerRepository
from trustworthy_kb.persistence.publication_repository import PublicationRepository


def _now() -> datetime:
    return datetime.now(UTC)


def _run(*, operation_id: str, question_hash: str, now: datetime) -> AnswerRunRecord:
    return AnswerRunRecord(
        id=AnswerRunId.generate(),
        operation_id=operation_id,
        question_hash=question_hash,
        scope=AnswerScope.GENERAL,
        status=AnswerRunStatus.IN_PROGRESS,
        model_name="sub2api/synthetic-model",
        prompt_version="answer-v1",
        revision=1,
        started_at=now,
        created_at=now,
        updated_at=now,
    )


async def test_answer_repository_persists_hash_only_terminal_outcomes(tmp_path: Path) -> None:
    database_path = tmp_path / "answers.db"
    engine = create_database_engine(
        DatabaseSettings(url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = create_session_factory(engine)()
    repository = AnswerRepository(session)
    publication = PublicationRepository(session)
    now = _now()
    try:
        generation = await publication.add_index_generation(
            IndexGenerationRecord(
                id=IndexGenerationId.generate(),
                generation_number=1,
                embedding_model="synthetic/embedding",
                chunker_version="markdown-v1",
                collection_name="trustworthy_kb_chunks_g1",
                embedding_dimension=32,
                schema_version="milvus-hybrid-v1",
                manifest_hash="a" * 64,
                status=IndexGenerationStatus.STAGING,
                revision=1,
                created_at=now,
            )
        )
        created = await repository.add_run(
            _run(operation_id="answer:synthetic", question_hash="b" * 64, now=now)
        )
        completed = await repository.complete_answer(
            created.id,
            generation_id=generation.id,
            plan_hash="c" * 64,
            answer_hash="d" * 64,
            citation_manifest_hash="e" * 64,
            expected_revision=created.revision,
        )
        refused_start = await repository.add_run(
            _run(operation_id="answer:refused", question_hash="f" * 64, now=now)
        )
        refused = await repository.refuse(
            refused_start.id,
            reason_code="NO_TRUSTED_EVIDENCE",
            expected_revision=refused_start.revision,
        )
        await session.commit()

        assert completed.status is AnswerRunStatus.ANSWERED
        assert completed.generation_id == generation.id
        assert completed.completed_at is not None
        assert await repository.find_run("answer:synthetic") == completed
        assert refused.status is AnswerRunStatus.REFUSED
        assert refused.refusal_code == "NO_TRUSTED_EVIDENCE"
    finally:
        await session.close()
        await engine.dispose()

    raw = database_path.read_bytes()
    assert b"private synthetic question body" not in raw
    assert b"private synthetic answer body" not in raw
