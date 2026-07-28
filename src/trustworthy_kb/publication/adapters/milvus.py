"""PyMilvus 2.6 adapter with BM25, dense search, RRF, and strong probes."""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

from trustworthy_kb.domain import (
    ClaimId,
    ClaimStatus,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeNoteId,
    Sensitivity,
)
from trustworthy_kb.publication.contracts import (
    IndexedChunk,
    IndexProbe,
    KnowledgeChunk,
    RetrievalQuery,
    VectorSearchHit,
    VectorSearchRequest,
    utc_milliseconds,
)

_COLLECTION = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OUTPUT_FIELDS = [
    "chunk_id",
    "text",
    "note_id",
    "curated_version_id",
    "claim_ids_json",
    "heading_path_json",
    "ordinal",
    "quality_status",
    "sensitivity",
    "valid_from_ms",
    "valid_to_ms",
    "freshness_at_ms",
    "generation_id",
    "generation_number",
    "embedding_model",
    "chunker_version",
    "content_hash",
]
_PROBE_FIELDS = ["chunk_id", "curated_version_id", "content_hash"]


class MilvusVectorIndex:
    """Store one immutable embedding schema per deterministic Collection generation."""

    def __init__(
        self,
        *,
        uri: str,
        token: str | None = None,
        collection_prefix: str = "trustworthy_kb_chunks_g",
        consistency: str = "Bounded",
        timeout_seconds: float = 30.0,
        client: Any | None = None,
        sdk: ModuleType | Any | None = None,
    ) -> None:
        if not _COLLECTION.fullmatch(collection_prefix) or timeout_seconds <= 0:
            raise ValueError("Milvus adapter configuration is invalid")
        if consistency not in {"Strong", "Bounded", "Session", "Eventually"}:
            raise ValueError("Milvus consistency level is invalid")
        self._sdk = sdk or _load_pymilvus()
        if client is None:
            kwargs: dict[str, object] = {"uri": uri, "timeout": timeout_seconds}
            if token:
                kwargs["token"] = token
            client = self._sdk.MilvusClient(**kwargs)
        self._client = client
        self._prefix = collection_prefix
        self._consistency = consistency
        self._timeout = timeout_seconds
        self._lock = asyncio.Lock()

    async def ensure_generation(self, *, generation_number: int, embedding_dimension: int) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._ensure_generation_sync, generation_number, embedding_dimension
            )

    async def upsert(self, generation_number: int, chunks: Sequence[IndexedChunk]) -> None:
        if not chunks:
            return
        rows = [milvus_row(item) for item in chunks]
        async with self._lock:
            await asyncio.to_thread(
                self._client.upsert,
                collection_name=self.collection_name(generation_number),
                data=rows,
                timeout=self._timeout,
            )

    async def fetch_probes(
        self, generation_number: int, chunk_ids: Sequence[str]
    ) -> tuple[IndexProbe, ...]:
        ids = _valid_chunk_ids(chunk_ids)
        if not ids:
            return ()
        rows = await asyncio.to_thread(
            self._client.query,
            collection_name=self.collection_name(generation_number),
            filter=f"chunk_id in {json.dumps(ids, separators=(',', ':'))}",
            output_fields=_PROBE_FIELDS,
            consistency_level="Strong",
            limit=len(ids),
            timeout=self._timeout,
        )
        return _probes(rows)

    async def hybrid_search(self, request: VectorSearchRequest) -> tuple[VectorSearchHit, ...]:
        expression = milvus_filter_expression(request.query, request.generation_number)
        collection = self.collection_name(request.generation_number)
        sparse_request = self._sdk.AnnSearchRequest(
            data=[request.query.text],
            anns_field="sparse",
            param={"metric_type": "BM25", "params": {}},
            limit=request.query.candidate_k,
            expr=expression,
        )
        if not request.dense:
            raw = await asyncio.to_thread(
                self._client.search,
                collection_name=collection,
                data=[request.query.text],
                anns_field="sparse",
                filter=expression,
                search_params={"metric_type": "BM25", "params": {}},
                output_fields=_OUTPUT_FIELDS,
                limit=request.query.candidate_k,
                consistency_level=self._consistency,
                timeout=self._timeout,
            )
        else:
            dense_request = self._sdk.AnnSearchRequest(
                data=[list(request.dense)],
                anns_field="dense",
                param={"metric_type": "COSINE", "params": {}},
                limit=request.query.candidate_k,
                expr=expression,
            )
            ranker = self._sdk.Function(
                name="rrf",
                input_field_names=[],
                function_type=self._sdk.FunctionType.RERANK,
                params={"reranker": "rrf", "k": request.rrf_k},
            )
            raw = await asyncio.to_thread(
                self._client.hybrid_search,
                collection_name=collection,
                reqs=[dense_request, sparse_request],
                ranker=ranker,
                limit=request.query.candidate_k,
                output_fields=_OUTPUT_FIELDS,
                consistency_level=self._consistency,
                timeout=self._timeout,
            )
        return _search_hits(raw)

    async def delete_chunks(self, generation_number: int, chunk_ids: Sequence[str]) -> None:
        ids = _valid_chunk_ids(chunk_ids)
        if not ids:
            return
        async with self._lock:
            await asyncio.to_thread(
                self._client.delete,
                collection_name=self.collection_name(generation_number),
                filter=f"chunk_id in {json.dumps(ids, separators=(',', ':'))}",
                timeout=self._timeout,
            )

    async def list_probes_for_version(
        self, generation_number: int, curated_version_id: CuratedVersionId
    ) -> tuple[IndexProbe, ...]:
        rows = await asyncio.to_thread(
            self._client.query,
            collection_name=self.collection_name(generation_number),
            filter=f"curated_version_id == {json.dumps(str(curated_version_id))}",
            output_fields=_PROBE_FIELDS,
            consistency_level="Strong",
            limit=16384,
            timeout=self._timeout,
        )
        return _probes(rows)

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)

    def collection_name(self, generation_number: int) -> str:
        """Return a safe deterministic Collection name."""

        if generation_number < 1:
            raise ValueError("index generation number must be positive")
        name = f"{self._prefix}{generation_number}"
        if len(name) > 255:
            raise ValueError("Milvus Collection name is too long")
        return name

    def _ensure_generation_sync(self, generation_number: int, dimension: int) -> None:
        if dimension < 2:
            raise ValueError("embedding dimension must be at least two")
        name = self.collection_name(generation_number)
        if self._client.has_collection(collection_name=name, timeout=self._timeout):
            description = self._client.describe_collection(
                collection_name=name, timeout=self._timeout
            )
            _validate_existing_schema(description, dimension)
            self._client.load_collection(collection_name=name, timeout=self._timeout)
            return
        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        data_type = self._sdk.DataType
        schema.add_field(
            field_name="chunk_id",
            datatype=data_type.VARCHAR,
            is_primary=True,
            max_length=64,
        )
        schema.add_field(
            field_name="text",
            datatype=data_type.VARCHAR,
            max_length=8192,
            enable_analyzer=True,
        )
        schema.add_field(field_name="sparse", datatype=data_type.SPARSE_FLOAT_VECTOR)
        schema.add_field(field_name="dense", datatype=data_type.FLOAT_VECTOR, dim=dimension)
        for field, length in (
            ("note_id", 64),
            ("curated_version_id", 64),
            ("claim_ids_json", 8192),
            ("heading_path_json", 2048),
            ("quality_status", 32),
            ("sensitivity", 16),
            ("generation_id", 64),
            ("embedding_model", 255),
            ("chunker_version", 64),
            ("content_hash", 64),
        ):
            schema.add_field(field_name=field, datatype=data_type.VARCHAR, max_length=length)
        for field in (
            "ordinal",
            "valid_from_ms",
            "valid_to_ms",
            "freshness_at_ms",
            "generation_number",
        ):
            schema.add_field(field_name=field, datatype=data_type.INT64)
        schema.add_function(
            self._sdk.Function(
                name="text_bm25",
                input_field_names=["text"],
                output_field_names=["sparse"],
                function_type=self._sdk.FunctionType.BM25,
            )
        )
        indexes = self._client.prepare_index_params()
        indexes.add_index(field_name="dense", index_type="AUTOINDEX", metric_type="COSINE")
        indexes.add_index(
            field_name="sparse",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75},
        )
        self._client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=indexes,
            consistency_level=self._consistency,
            timeout=self._timeout,
        )
        self._client.load_collection(collection_name=name, timeout=self._timeout)


def milvus_row(item: IndexedChunk) -> dict[str, object]:
    """Map one provider-neutral Chunk to explicit Milvus scalar fields."""

    chunk = item.chunk
    return {
        "chunk_id": chunk.chunk_id,
        "text": chunk.text,
        "dense": list(item.dense),
        "note_id": str(chunk.note_id),
        "curated_version_id": str(chunk.curated_version_id),
        "claim_ids_json": json.dumps(
            [str(value) for value in chunk.claim_ids], separators=(",", ":")
        ),
        "heading_path_json": json.dumps(
            list(chunk.heading_path), ensure_ascii=False, separators=(",", ":")
        ),
        "ordinal": chunk.ordinal,
        "quality_status": chunk.quality_status.value,
        "sensitivity": chunk.sensitivity.value,
        "valid_from_ms": utc_milliseconds(chunk.valid_from),
        "valid_to_ms": utc_milliseconds(chunk.valid_to),
        "freshness_at_ms": utc_milliseconds(chunk.freshness_at),
        "generation_id": str(chunk.generation_id),
        "generation_number": chunk.generation_number,
        "embedding_model": chunk.embedding_model,
        "chunker_version": chunk.chunker_version,
        "content_hash": chunk.content_hash,
    }


def milvus_filter_expression(query: RetrievalQuery, generation_number: int) -> str:
    """Build a value-safe expression from enums, integers, and validated configuration."""

    statuses = json.dumps(
        [value.value for value in query.allowed_quality_statuses], separators=(",", ":")
    )
    sensitivity_order = {
        Sensitivity.PUBLIC: (Sensitivity.PUBLIC,),
        Sensitivity.RESTRICTED: (Sensitivity.PUBLIC, Sensitivity.RESTRICTED),
        Sensitivity.PRIVATE: (
            Sensitivity.PUBLIC,
            Sensitivity.RESTRICTED,
            Sensitivity.PRIVATE,
        ),
    }
    sensitivities = json.dumps(
        [value.value for value in sensitivity_order[query.max_sensitivity]],
        separators=(",", ":"),
    )
    at = utc_milliseconds(query.at)
    terms = [
        f"generation_number == {generation_number}",
        f"quality_status in {statuses}",
        f"sensitivity in {sensitivities}",
        f"(valid_from_ms == 0 or valid_from_ms <= {at})",
        f"(valid_to_ms == 0 or valid_to_ms >= {at})",
    ]
    if not query.allow_stale:
        terms.append(f"(freshness_at_ms == 0 or freshness_at_ms >= {at})")
    return " and ".join(terms)


def _validate_existing_schema(description: object, dimension: int) -> None:
    if not isinstance(description, Mapping):
        raise RuntimeError("Milvus Collection description is invalid")
    raw_fields = description.get("fields")
    if not isinstance(raw_fields, Sequence):
        raise RuntimeError("Milvus Collection fields are unavailable")
    fields: dict[str, Mapping[str, object]] = {}
    for raw in raw_fields:
        if isinstance(raw, Mapping):
            name = raw.get("name") or raw.get("field_name")
            if isinstance(name, str):
                fields[name] = raw
    required = set(_OUTPUT_FIELDS) | {"dense", "sparse"}
    if not required.issubset(fields):
        raise RuntimeError("Milvus Collection schema does not match the L4 contract")
    dense = fields["dense"]
    params = dense.get("params") or dense.get("type_params") or {}
    actual_dimension = params.get("dim") if isinstance(params, Mapping) else None
    actual_dimension = dense.get("dim") if actual_dimension is None else actual_dimension
    if actual_dimension is None or int(str(actual_dimension)) != dimension:
        raise RuntimeError("Milvus Collection embedding dimension does not match")


def _valid_chunk_ids(values: Sequence[str]) -> list[str]:
    unique = list(dict.fromkeys(values))
    if any(not _SHA256.fullmatch(value) for value in unique):
        raise ValueError("Chunk ID filter is invalid")
    return unique


def _probes(rows: object) -> tuple[IndexProbe, ...]:
    if not isinstance(rows, Sequence):
        raise RuntimeError("Milvus query returned an invalid response")
    return tuple(
        IndexProbe(
            chunk_id=str(row["chunk_id"]),
            curated_version_id=CuratedVersionId(str(row["curated_version_id"])),
            content_hash=str(row["content_hash"]),
        )
        for row in rows
        if isinstance(row, Mapping)
    )


def _search_hits(raw: object) -> tuple[VectorSearchHit, ...]:
    if not isinstance(raw, Sequence) or not raw:
        return ()
    first = raw[0]
    if not isinstance(first, Sequence):
        raise RuntimeError("Milvus search returned an invalid response")
    hits: list[VectorSearchHit] = []
    for hit in first:
        entity, score = _hit_entity(hit)
        hits.append(VectorSearchHit(chunk=_chunk_from_entity(entity), score=score))
    return tuple(hits)


def _hit_entity(hit: object) -> tuple[Mapping[str, object], float]:
    if isinstance(hit, Mapping):
        raw_entity = hit.get("entity", hit)
        raw_score = hit.get("distance", hit.get("score"))
    else:
        getter = getattr(hit, "get", None)
        raw_entity = (
            {field: getter(field) for field in _OUTPUT_FIELDS} if callable(getter) else None
        )
        raw_score = getattr(hit, "distance", getattr(hit, "score", None))
    if not isinstance(raw_entity, Mapping):
        raise RuntimeError("Milvus search hit entity is invalid")
    score = float(raw_score)
    if not math.isfinite(score):
        raise RuntimeError("Milvus search hit score is invalid")
    entity = dict(raw_entity)
    if "chunk_id" not in entity and isinstance(hit, Mapping) and "id" in hit:
        entity["chunk_id"] = hit["id"]
    return entity, score


def _chunk_from_entity(entity: Mapping[str, object]) -> KnowledgeChunk:
    try:
        claim_values = json.loads(str(entity["claim_ids_json"]))
        heading_values = json.loads(str(entity["heading_path_json"]))
        return KnowledgeChunk(
            chunk_id=str(entity["chunk_id"]),
            note_id=KnowledgeNoteId(str(entity["note_id"])),
            curated_version_id=CuratedVersionId(str(entity["curated_version_id"])),
            claim_ids=tuple(ClaimId(str(value)) for value in claim_values),
            text=str(entity["text"]),
            heading_path=tuple(str(value) for value in heading_values),
            ordinal=int(str(entity["ordinal"])),
            quality_status=ClaimStatus(str(entity["quality_status"])),
            sensitivity=Sensitivity(str(entity["sensitivity"])),
            valid_from=_datetime_from_ms(entity["valid_from_ms"]),
            valid_to=_datetime_from_ms(entity["valid_to_ms"]),
            freshness_at=_datetime_from_ms(entity["freshness_at_ms"]),
            generation_id=IndexGenerationId(str(entity["generation_id"])),
            generation_number=int(str(entity["generation_number"])),
            embedding_model=str(entity["embedding_model"]),
            chunker_version=str(entity["chunker_version"]),
            content_hash=str(entity["content_hash"]),
        )
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Milvus search hit metadata failed validation") from None


def _datetime_from_ms(value: object) -> datetime | None:
    milliseconds = int(str(value))
    return None if milliseconds == 0 else datetime.fromtimestamp(milliseconds / 1000, UTC)


def _load_pymilvus() -> ModuleType:
    try:
        return importlib.import_module("pymilvus")
    except ImportError:
        raise RuntimeError(
            "Milvus support is not installed; run 'uv sync --extra retrieval'"
        ) from None


__all__ = ["MilvusVectorIndex", "milvus_filter_expression", "milvus_row"]
