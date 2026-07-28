"""Lifespan-owned composition root for the trusted answer API."""

from __future__ import annotations

import os
from typing import cast

from sqlalchemy.ext.asyncio import AsyncEngine

from trustworthy_kb.answer import (
    AnswerCitationVerifier,
    AnswerPlanner,
    AnswerSnapshotStore,
    SqliteAnswerEvidenceResolver,
    StructuredAnswerGenerator,
    TrustedAnswerService,
)
from trustworthy_kb.config import (
    AnswerSettings,
    DatabaseSettings,
    LLMSettings,
    PublicationSettings,
    RetrievalSettings,
)
from trustworthy_kb.governance.audit import AuditedModelGateway
from trustworthy_kb.llm import ModelGateway, ModelRouter
from trustworthy_kb.persistence import (
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)
from trustworthy_kb.persistence.migrations import assert_schema_current
from trustworthy_kb.publication.adapters import (
    BgeM3Embedding,
    BgeReranker,
    MilvusVectorIndex,
    SqliteCurrentVersionResolver,
)
from trustworthy_kb.publication.retrieval import HybridRetriever
from trustworthy_kb.publication.snapshot_store import PublicationSnapshotStore


class AnswerRuntime:
    """Own expensive model, vector, and database resources for one API process."""

    def __init__(self) -> None:
        self.database = DatabaseSettings(_env_file=".env")
        self.answer_settings = AnswerSettings(_env_file=".env")
        self.publication = PublicationSettings(_env_file=".env")
        self.retrieval = RetrievalSettings(_env_file=".env")
        self.llm = LLMSettings(_env_file=".env")
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
        embedding = BgeM3Embedding(
            model_name=self.retrieval.embedding_model,
            dimension=self.retrieval.embedding_dimension,
            device=self.retrieval.embedding_device,
            batch_size=self.retrieval.embedding_batch_size,
            cache_dir=self.retrieval.model_cache_root_value / "hub",
        )
        reranker = (
            None
            if self.retrieval.reranker_provider == "none"
            else BgeReranker(
                model_name=self.retrieval.reranker_model,
                device=self.retrieval.reranker_device,
                batch_size=self.retrieval.reranker_batch_size,
                cache_dir=self.retrieval.model_cache_root_value / "hub",
            )
        )
        audited = AuditedModelGateway(
            ModelGateway(ModelRouter(self.llm)),
            self.factory,
            self.llm,
        )
        gateway = cast(ModelGateway, audited)
        self.service = TrustedAnswerService(
            unit_of_work_factory=self.factory,
            planner=AnswerPlanner(gateway, prompt_version=self.answer_settings.prompt_version),
            retriever=HybridRetriever(
                embedding=embedding,
                index=self.index,
                current_versions=SqliteCurrentVersionResolver(self.factory),
                reranker=reranker,
                allow_bm25_only=self.retrieval.allow_bm25_only,
                rrf_k=self.retrieval.rrf_k,
            ),
            evidence_resolver=SqliteAnswerEvidenceResolver(
                self.factory,
                PublicationSnapshotStore(self.publication.snapshot_root_value),
            ),
            generator=StructuredAnswerGenerator(
                gateway,
                prompt_version=self.answer_settings.prompt_version,
                max_claims=self.answer_settings.max_answer_claims,
                max_claim_characters=self.answer_settings.max_claim_characters,
            ),
            verifier=AnswerCitationVerifier(
                gateway,
                prompt_version=self.answer_settings.citation_verifier_version,
            ),
            snapshots=AnswerSnapshotStore(self.answer_settings.snapshot_root_value),
            settings=self.answer_settings,
            model_name=f"{self.llm.provider}/{self.llm.answer_model or self.llm.model}",
        )

    async def initialize(self) -> None:
        await assert_schema_current(self.engine)

    async def ready(self) -> bool:
        try:
            async with self.factory() as unit_of_work:
                generation = await unit_of_work.publication.get_active_index_generation()
            return generation is not None and await self.index.has_generation(
                generation.generation_number
            )
        except Exception:
            return False

    async def close(self) -> None:
        await self.index.close()
        await self.engine.dispose()


__all__ = ["AnswerRuntime"]
