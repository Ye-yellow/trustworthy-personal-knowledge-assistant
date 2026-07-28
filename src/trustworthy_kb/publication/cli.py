"""Local CLI for L4 index generations, publication, and hybrid retrieval."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from trustworthy_kb.config import (
    DatabaseSettings,
    GovernanceSettings,
    LLMSettings,
    PublicationSettings,
    RetrievalSettings,
)
from trustworthy_kb.domain import (
    IndexGenerationId,
    IndexGenerationRecord,
    IndexGenerationStatus,
    KnowledgeChangeId,
    Sensitivity,
)
from trustworthy_kb.governance.audit import AuditedModelGateway
from trustworthy_kb.governance.fingerprints import canonical_json_hash
from trustworthy_kb.llm import ModelGateway, ModelRouter
from trustworthy_kb.persistence import (
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)
from trustworthy_kb.persistence.base import utc_now
from trustworthy_kb.persistence.errors import PersistenceError
from trustworthy_kb.persistence.migrations import assert_schema_current
from trustworthy_kb.publication.adapters import (
    BgeM3Embedding,
    BgeReranker,
    MilvusVectorIndex,
    SqliteCurrentVersionResolver,
)
from trustworthy_kb.publication.chunking import MarkdownChunker
from trustworthy_kb.publication.contracts import ExpectedPublication, RetrievalQuery
from trustworthy_kb.publication.curation import (
    CuratedMarkdownRenderer,
    ModelCurationPlanner,
)
from trustworthy_kb.publication.errors import PublicationError, RetrievalError
from trustworthy_kb.publication.indexing import GenerationIndexer
from trustworthy_kb.publication.reconciliation import PublicationReconciler
from trustworthy_kb.publication.retrieval import HybridRetriever
from trustworthy_kb.publication.runner import PublicationRunner
from trustworthy_kb.publication.snapshot_store import PublicationSnapshotStore
from trustworthy_kb.publication.vault import AtomicVaultPublisher


class _Runtime:
    def __init__(self) -> None:
        self.database = DatabaseSettings(_env_file=".env")
        self.publication = PublicationSettings(_env_file=".env")
        self.retrieval = RetrievalSettings(_env_file=".env")
        os.environ.setdefault("HF_HOME", str(self.retrieval.model_cache_root_value))
        self.engine: AsyncEngine = create_database_engine(self.database)
        self.factory = SqliteUnitOfWorkFactory(create_session_factory(self.engine))
        self.index = MilvusVectorIndex(
            uri=self.retrieval.milvus_uri,
            token=self.retrieval.milvus_token_value,
            collection_prefix=self.retrieval.collection_prefix,
            consistency=self.retrieval.consistency,
            timeout_seconds=self.retrieval.timeout_seconds,
        )

    async def initialize(self) -> None:
        await assert_schema_current(self.engine)

    async def close(self) -> None:
        await self.index.close()
        await self.engine.dispose()

    def embedding(self) -> BgeM3Embedding:
        return BgeM3Embedding(
            model_name=self.retrieval.embedding_model,
            dimension=self.retrieval.embedding_dimension,
            device=self.retrieval.embedding_device,
            batch_size=self.retrieval.embedding_batch_size,
            cache_dir=self.retrieval.model_cache_root_value / "hub",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trustworthy-kb-publication")
    commands = parser.add_subparsers(dest="command", required=True)
    generation = commands.add_parser("generation", help="manage immutable index generations")
    generation_commands = generation.add_subparsers(dest="generation_command", required=True)
    generation_commands.add_parser("create", help="create or reuse the configured generation")

    publish = commands.add_parser("publish", help="publish one governed knowledge change")
    publish.add_argument("change_id")
    publish.add_argument("--path", required=True, help="Vault-relative generated note path")
    publish.add_argument("--generation-id")
    publish.add_argument("--operation-id")

    retrieve = commands.add_parser("retrieve", help="run current-version hybrid retrieval")
    retrieve.add_argument("query")
    retrieve.add_argument("--top-k", type=int, default=5)
    retrieve.add_argument("--allow-stale", action="store_true")

    reconcile = commands.add_parser("reconcile", help="verify Vault and repair index drift")
    reconcile.add_argument("--generation-id")
    reconcile.add_argument("--no-repair", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> object:
    runtime = _Runtime()
    try:
        await runtime.initialize()
        if args.command == "generation":
            return (await _create_generation(runtime)).model_dump(mode="json")
        generation = await _select_generation(runtime, getattr(args, "generation_id", None))
        if args.command == "publish":
            return await _publish(runtime, args, generation)
        if args.command == "reconcile":
            return await _reconcile(runtime, args, generation)
        return await _retrieve(runtime, args, generation)
    finally:
        await runtime.close()


async def _create_generation(runtime: _Runtime) -> IndexGenerationRecord:
    async with runtime.factory() as unit_of_work:
        generations = await unit_of_work.publication.list_index_generations()
    matching = next(
        (
            item
            for item in reversed(generations)
            if item.status in {IndexGenerationStatus.STAGING, IndexGenerationStatus.ACTIVE}
            and _generation_matches(runtime, item)
        ),
        None,
    )
    if matching is not None:
        await runtime.index.ensure_generation(
            generation_number=matching.generation_number,
            embedding_dimension=matching.embedding_dimension,
        )
        return matching
    number = max((item.generation_number for item in generations), default=0) + 1
    await runtime.index.ensure_generation(
        generation_number=number,
        embedding_dimension=runtime.retrieval.embedding_dimension,
    )
    timestamp = utc_now()
    record = IndexGenerationRecord(
        id=IndexGenerationId.generate(),
        generation_number=number,
        embedding_model=runtime.retrieval.embedding_model,
        chunker_version=runtime.publication.chunker_version,
        collection_name=runtime.index.collection_name(number),
        embedding_dimension=runtime.retrieval.embedding_dimension,
        schema_version=runtime.publication.schema_version,
        manifest_hash=canonical_json_hash(
            {
                "embedding_model": runtime.retrieval.embedding_model,
                "embedding_dimension": runtime.retrieval.embedding_dimension,
                "chunker_version": runtime.publication.chunker_version,
                "schema_version": runtime.publication.schema_version,
            }
        ),
        status=IndexGenerationStatus.STAGING,
        revision=1,
        created_at=timestamp,
    )
    async with runtime.factory() as unit_of_work:
        created = await unit_of_work.publication.add_index_generation(record)
        await unit_of_work.commit()
    return created


async def _select_generation(
    runtime: _Runtime, raw_generation_id: str | None
) -> IndexGenerationRecord:
    generation: IndexGenerationRecord | None
    async with runtime.factory() as unit_of_work:
        if raw_generation_id:
            generation = await unit_of_work.publication.get_index_generation(
                IndexGenerationId(raw_generation_id)
            )
        else:
            generation = await unit_of_work.publication.get_active_index_generation()
            if generation is None:
                generations = await unit_of_work.publication.list_index_generations()
                generation = next(
                    (
                        item
                        for item in reversed(generations)
                        if item.status is IndexGenerationStatus.STAGING
                    ),
                    None,
                )
    if generation is None:
        raise PublicationError("no index generation exists; run 'generation create' first")
    if not _generation_matches(runtime, generation):
        raise PublicationError("selected index generation does not match retrieval configuration")
    return generation


async def _publish(
    runtime: _Runtime,
    args: argparse.Namespace,
    generation: IndexGenerationRecord,
) -> object:
    embedding = runtime.embedding()
    llm = LLMSettings(_env_file=".env")
    governance = GovernanceSettings(_env_file=".env")
    gateway = AuditedModelGateway(
        ModelGateway(ModelRouter(llm)),
        runtime.factory,
        llm,
    )
    runner = PublicationRunner(
        unit_of_work_factory=runtime.factory,
        planner=ModelCurationPlanner(
            cast(ModelGateway, gateway),
            prompt_version=runtime.publication.prompt_version,
        ),
        renderer=CuratedMarkdownRenderer(),
        chunker=MarkdownChunker(version=runtime.publication.chunker_version),
        vault=AtomicVaultPublisher(
            runtime.publication.vault_path_value,
            staging_root=runtime.publication.staging_root,
            versions_root=runtime.publication.versions_root,
            max_bytes=runtime.publication.max_markdown_bytes,
        ),
        indexer=GenerationIndexer(embedding, runtime.index),
        snapshots=PublicationSnapshotStore(runtime.publication.snapshot_root_value),
        model_name=f"{llm.provider}/{llm.curation_model or llm.model}",
        prompt_version=runtime.publication.prompt_version,
        quality_policy_version=governance.policy_version,
    )
    change_id = KnowledgeChangeId(args.change_id)
    operation_id = args.operation_id or f"publish:{change_id}:{generation.id}"
    report = await runner.publish(
        change_id=change_id,
        generation_id=generation.id,
        final_relative_path=args.path,
        operation_id=operation_id,
    )
    return report.model_dump(mode="json")


async def _retrieve(
    runtime: _Runtime,
    args: argparse.Namespace,
    generation: IndexGenerationRecord,
) -> object:
    if generation.status is not IndexGenerationStatus.ACTIVE:
        raise RetrievalError("retrieval requires an active index generation")
    embedding = runtime.embedding()
    reranker = (
        None
        if runtime.retrieval.reranker_provider == "none"
        else BgeReranker(
            model_name=runtime.retrieval.reranker_model,
            device=runtime.retrieval.reranker_device,
            batch_size=runtime.retrieval.reranker_batch_size,
            cache_dir=runtime.retrieval.model_cache_root_value / "hub",
        )
    )
    result = await HybridRetriever(
        embedding=embedding,
        index=runtime.index,
        current_versions=SqliteCurrentVersionResolver(runtime.factory),
        reranker=reranker,
        allow_bm25_only=runtime.retrieval.allow_bm25_only,
        rrf_k=runtime.retrieval.rrf_k,
    ).retrieve(
        RetrievalQuery(
            text=args.query,
            top_k=args.top_k,
            candidate_k=min(500, max(args.top_k * 6, 30)),
            max_sensitivity=Sensitivity.PRIVATE,
            at=datetime.now(UTC),
            allow_stale=args.allow_stale,
        ),
        generation_id=generation.id,
        generation_number=generation.generation_number,
    )
    return {
        "mode": result.mode.value,
        "degraded": result.degraded,
        "generation_id": str(result.generation_id),
        "hits": [
            {
                "chunk_id": hit.chunk.chunk_id,
                "note_id": str(hit.chunk.note_id),
                "curated_version_id": str(hit.chunk.curated_version_id),
                "heading_path": list(hit.chunk.heading_path),
                "text": hit.chunk.text,
                "retrieval_score": hit.retrieval_score,
                "rerank_score": hit.rerank_score,
            }
            for hit in result.hits
        ],
    }


async def _reconcile(
    runtime: _Runtime,
    args: argparse.Namespace,
    generation: IndexGenerationRecord,
) -> object:
    embedding = runtime.embedding()
    snapshots = PublicationSnapshotStore(runtime.publication.snapshot_root_value)
    chunker = MarkdownChunker(version=runtime.publication.chunker_version)
    async with runtime.factory() as unit_of_work:
        notes = await unit_of_work.publication.list_active_notes(generation.id)
        rows = []
        for note in notes:
            if note.current_curated_version_id is None:
                continue
            version = await unit_of_work.publication.get_curated_version(
                note.current_curated_version_id
            )
            rows.append((note, version))
    expected = []
    for note, version in rows:
        snapshot = await snapshots.get(version.content_hash)
        artifact = snapshot.artifact
        claims = snapshot.claims
        expected.append(
            ExpectedPublication(
                artifact=artifact,
                final_relative_path=note.canonical_path,
                generation_number=generation.generation_number,
                chunks=chunker.chunk(
                    artifact,
                    claims,
                    generation_id=generation.id,
                    generation_number=generation.generation_number,
                    embedding_model=generation.embedding_model,
                ),
            )
        )
    report = await PublicationReconciler(
        vault=AtomicVaultPublisher(
            runtime.publication.vault_path_value,
            staging_root=runtime.publication.staging_root,
            versions_root=runtime.publication.versions_root,
            max_bytes=runtime.publication.max_markdown_bytes,
        ),
        index=runtime.index,
        indexer=GenerationIndexer(embedding, runtime.index),
        repair_index=not args.no_repair,
    ).reconcile(expected)
    return report.model_dump(mode="json")


def _generation_matches(runtime: _Runtime, generation: IndexGenerationRecord) -> bool:
    return (
        generation.embedding_model == runtime.retrieval.embedding_model
        and generation.embedding_dimension == runtime.retrieval.embedding_dimension
        and generation.chunker_version == runtime.publication.chunker_version
        and generation.schema_version == runtime.publication.schema_version
        and generation.collection_name
        == runtime.index.collection_name(generation.generation_number)
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception as error:
        print(_safe_error(error), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _safe_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "publication configuration or identifier is invalid"
    if isinstance(error, (PublicationError, RetrievalError, PersistenceError)):
        return str(error)
    return "publication command failed"


__all__ = ["main"]
