from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from trustworthy_kb.domain import (
    ClaimId,
    ClaimStatus,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeNoteId,
    Sensitivity,
)
from trustworthy_kb.publication.adapters.milvus import (
    MilvusVectorIndex,
    milvus_filter_expression,
    milvus_row,
)
from trustworthy_kb.publication.contracts import (
    IndexedChunk,
    KnowledgeChunk,
    RetrievalQuery,
    VectorSearchRequest,
)


class _Schema:
    def __init__(self) -> None:
        self.fields: list[dict[str, object]] = []
        self.functions: list[object] = []

    def add_field(self, **kwargs: object) -> None:
        self.fields.append({"name": kwargs["field_name"], **kwargs})

    def add_function(self, function: object) -> None:
        self.functions.append(function)


class _Indexes:
    def __init__(self) -> None:
        self.values: list[dict[str, object]] = []

    def add_index(self, **kwargs: object) -> None:
        self.values.append(dict(kwargs))


class _Client:
    def __init__(self) -> None:
        self.schema: _Schema | None = None
        self.rows: dict[str, dict[str, object]] = {}

    def has_collection(self, **_kwargs: object) -> bool:
        return self.schema is not None

    def create_schema(self, **_kwargs: object) -> _Schema:
        return _Schema()

    def prepare_index_params(self) -> _Indexes:
        return _Indexes()

    def create_collection(self, **kwargs: object) -> None:
        self.schema = kwargs["schema"]  # type: ignore[assignment]

    def describe_collection(self, **_kwargs: object) -> dict[str, object]:
        assert self.schema is not None
        return {"fields": self.schema.fields}

    def load_collection(self, **_kwargs: object) -> None:
        return None

    def upsert(self, **kwargs: object) -> None:
        for row in kwargs["data"]:  # type: ignore[union-attr]
            self.rows[row["chunk_id"]] = row

    def query(self, **kwargs: object) -> list[dict[str, object]]:
        expression = str(kwargs["filter"])
        if expression.startswith("chunk_id in "):
            ids = json.loads(expression.removeprefix("chunk_id in "))
            return [self.rows[value] for value in ids if value in self.rows]
        value = json.loads(expression.split(" == ", maxsplit=1)[1])
        return [row for row in self.rows.values() if row["curated_version_id"] == value]

    def hybrid_search(self, **_kwargs: object) -> list[list[dict[str, object]]]:
        return self._hits()

    def search(self, **_kwargs: object) -> list[list[dict[str, object]]]:
        return self._hits()

    def delete(self, **kwargs: object) -> None:
        ids = json.loads(str(kwargs["filter"]).removeprefix("chunk_id in "))
        for value in ids:
            self.rows.pop(value, None)

    def _hits(self) -> list[list[dict[str, object]]]:
        return [
            [{"id": row["chunk_id"], "distance": 0.5, "entity": row} for row in self.rows.values()]
        ]


def _sdk() -> SimpleNamespace:
    data_type = SimpleNamespace(
        VARCHAR="VARCHAR",
        SPARSE_FLOAT_VECTOR="SPARSE",
        FLOAT_VECTOR="FLOAT",
        INT64="INT64",
    )
    function_type = SimpleNamespace(BM25="BM25", RERANK="RERANK")
    return SimpleNamespace(
        DataType=data_type,
        FunctionType=function_type,
        Function=lambda **kwargs: kwargs,
        AnnSearchRequest=lambda **kwargs: kwargs,
    )


def _indexed() -> IndexedChunk:
    now = datetime.now(UTC).replace(microsecond=0)
    return IndexedChunk(
        chunk=KnowledgeChunk(
            chunk_id="1" * 64,
            note_id=KnowledgeNoteId.generate(),
            curated_version_id=CuratedVersionId.generate(),
            claim_ids=(ClaimId.generate(),),
            text="Milvus hybrid retrieval",
            heading_path=("Milvus", "Search"),
            ordinal=0,
            quality_status=ClaimStatus.VERIFIED,
            sensitivity=Sensitivity.PRIVATE,
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=1),
            freshness_at=now + timedelta(days=1),
            generation_id=IndexGenerationId.generate(),
            generation_number=1,
            embedding_model="synthetic/embedding",
            chunker_version="markdown-v1",
            content_hash="2" * 64,
        ),
        dense=(1.0, 0.0, 0.0),
    )


async def test_milvus_adapter_creates_upserts_searches_probes_and_deletes() -> None:
    client = _Client()
    adapter = MilvusVectorIndex(
        uri="http://localhost:19530",
        client=client,
        sdk=_sdk(),
    )
    item = _indexed()

    await adapter.ensure_generation(generation_number=1, embedding_dimension=3)
    await adapter.ensure_generation(generation_number=1, embedding_dimension=3)
    await adapter.upsert(1, (item,))

    assert (await adapter.fetch_probes(1, (item.chunk.chunk_id,)))[0].content_hash == "2" * 64
    query = RetrievalQuery(
        text="hybrid",
        at=datetime.now(UTC),
        max_sensitivity=Sensitivity.PRIVATE,
    )
    hits = await adapter.hybrid_search(
        VectorSearchRequest(query=query, dense=(1.0, 0.0, 0.0), generation_number=1)
    )
    assert hits[0].chunk == item.chunk
    assert (await adapter.list_probes_for_version(1, item.chunk.curated_version_id))[
        0
    ].chunk_id == (item.chunk.chunk_id)

    await adapter.delete_chunks(1, (item.chunk.chunk_id,))
    assert await adapter.fetch_probes(1, (item.chunk.chunk_id,)) == ()


def test_milvus_row_and_filter_are_explicit_and_safe() -> None:
    item = _indexed()
    row = milvus_row(item)
    query = RetrievalQuery(
        text="query",
        at=datetime.now(UTC),
        max_sensitivity=Sensitivity.RESTRICTED,
    )
    expression = milvus_filter_expression(query, 7)

    assert row["generation_id"] == str(item.chunk.generation_id)
    assert row["heading_path_json"] == '["Milvus","Search"]'
    assert "generation_number == 7" in expression
    assert 'quality_status in ["VERIFIED"]' in expression
    assert 'sensitivity in ["public","restricted"]' in expression
    assert "freshness_at_ms" in expression
