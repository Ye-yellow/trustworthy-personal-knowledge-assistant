from __future__ import annotations

import math

import pytest

from trustworthy_kb.publication.adapters.bge import BgeM3Embedding, BgeReranker
from trustworthy_kb.publication.contracts import RerankItem


class _EmbeddingModel:
    def encode(self, sentences: list[str], **kwargs: object) -> dict[str, object]:
        assert kwargs["return_dense"] is True
        return {"dense_vecs": [[3.0, 4.0, 0.0] for _ in sentences]}


class _RerankerModel:
    def compute_score(self, pairs: list[list[str]], **kwargs: object) -> list[float]:
        assert kwargs["normalize"] is True
        return [0.2 if "first" in pair[1] else 0.9 for pair in pairs]


async def test_bge_embedding_normalizes_and_validates_vectors() -> None:
    embedding = BgeM3Embedding(
        model_name="synthetic/bge",
        dimension=3,
        device="cpu",
        model_factory=lambda *_args, **_kwargs: _EmbeddingModel(),
    )

    vectors = await embedding.embed_documents(("one", "two"))

    assert embedding.model_name == "synthetic/bge"
    assert len(vectors) == 2
    assert math.isclose(sum(value * value for value in vectors[0]), 1.0)
    assert await embedding.embed_query("query") == vectors[0]
    with pytest.raises(ValueError, match="empty"):
        await embedding.embed_documents(())


async def test_bge_reranker_orders_candidates_and_rejects_bad_request() -> None:
    reranker = BgeReranker(
        model_name="synthetic/reranker",
        device="cpu",
        model_factory=lambda *_args, **_kwargs: _RerankerModel(),
    )
    candidates = (
        RerankItem(chunk_id="1" * 64, text="first document", score=0.8),
        RerankItem(chunk_id="2" * 64, text="second document", score=0.1),
    )

    result = await reranker.rerank("query", candidates, top_k=1)

    assert result[0].chunk_id == "2" * 64
    assert result[0].score == 0.9
    with pytest.raises(ValueError, match="invalid"):
        await reranker.rerank("", candidates, top_k=1)
