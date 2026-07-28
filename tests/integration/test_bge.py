from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from trustworthy_kb.config import RetrievalSettings
from trustworthy_kb.publication.adapters.bge import BgeM3Embedding, BgeReranker
from trustworthy_kb.publication.contracts import RerankItem


@pytest.mark.integration
async def test_real_bge_embedding_and_reranker_on_synthetic_text() -> None:
    if os.environ.get("TRUSTKB_RUN_BGE_INTEGRATION") != "1":
        pytest.skip("set TRUSTKB_RUN_BGE_INTEGRATION=1 to load local BGE weights")

    settings = RetrievalSettings(_env_file=".env")
    cache_root = Path("storage/model-cache").resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_root)
    embedding = BgeM3Embedding(
        model_name=settings.embedding_model,
        dimension=settings.embedding_dimension,
        device="cpu",
        batch_size=1,
        max_length=128,
        use_fp16=False,
        cache_dir=cache_root / "hub",
    )
    vectors = await embedding.embed_documents(
        (
            "可信知识助手使用证据血缘验证回答。",
            "A grocery reminder mentions synthetic apples.",
        )
    )

    assert len(vectors) == 2
    assert all(len(vector) == settings.embedding_dimension for vector in vectors)
    assert all(
        math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0) for vector in vectors
    )

    reranker = BgeReranker(
        model_name=settings.reranker_model,
        device="cpu",
        batch_size=1,
        max_length=128,
        use_fp16=False,
        cache_dir=cache_root / "hub",
    )
    ranked = await reranker.rerank(
        "可信回答如何验证?",
        (
            RerankItem(
                chunk_id="a" * 64,
                text="可信知识助手使用证据血缘验证回答。",
                score=0.0,
            ),
            RerankItem(
                chunk_id="b" * 64,
                text="A grocery reminder mentions synthetic apples.",
                score=0.0,
            ),
        ),
        top_k=2,
    )

    assert [item.chunk_id for item in ranked] == ["a" * 64, "b" * 64]
    assert all(math.isfinite(item.score) for item in ranked)
