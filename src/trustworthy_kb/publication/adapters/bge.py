"""Optional local BGE embedding and reranking adapters."""

from __future__ import annotations

import asyncio
import importlib
import math
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from numbers import Real
from pathlib import Path
from typing import Any, Protocol, cast

from trustworthy_kb.publication.contracts import RerankItem


class _EmbeddingModel(Protocol):
    def encode(self, sentences: Sequence[str], **kwargs: Any) -> Mapping[str, Any]: ...


class _RerankerModel(Protocol):
    def compute_score(self, sentence_pairs: Sequence[Sequence[str]], **kwargs: Any) -> Any: ...


class BgeM3Embedding:
    """Run BGE-M3 locally behind the provider-neutral embedding port."""

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-m3",
        dimension: int = 1024,
        device: str | None = None,
        batch_size: int = 16,
        max_length: int = 8192,
        use_fp16: bool | None = None,
        cache_dir: str | Path | None = None,
        model_factory: Callable[..., object] | None = None,
    ) -> None:
        if not model_name.strip() or dimension < 2 or batch_size < 1 or max_length < 1:
            raise ValueError("BGE embedding configuration is invalid")
        if model_factory is None:
            model_source = _resolve_model_source(model_name, cache_dir=cache_dir)
            factory = _flag_embedding_factory("BGEM3FlagModel")
        else:
            model_source = model_name
            factory = model_factory
        kwargs: dict[str, object] = {
            "use_fp16": (_device_supports_fp16(device) if use_fp16 is None else use_fp16)
        }
        if cache_dir is not None:
            kwargs["cache_dir"] = str(Path(cache_dir))
        if device is not None:
            kwargs["devices"] = device
        self._model = cast(_EmbeddingModel, factory(model_source, **kwargs))
        self._model_name = model_name.strip()
        self._dimension = dimension
        self._batch_size = batch_size
        self._max_length = max_length
        self._lock = asyncio.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("BGE document inputs must not be empty")
        async with self._lock:
            result = await asyncio.to_thread(
                self._model.encode,
                list(texts),
                batch_size=self._batch_size,
                max_length=self._max_length,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
            )
        vectors = result.get("dense_vecs")
        if vectors is None:
            raise RuntimeError("BGE model did not return dense vectors")
        converted = tuple(_normalized_vector(item, self._dimension) for item in vectors)
        if len(converted) != len(texts):
            raise RuntimeError("BGE model returned an incomplete vector batch")
        return converted

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return (await self.embed_documents((text,)))[0]


class BgeReranker:
    """Run the local multilingual BGE cross-encoder reranker."""

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str | None = None,
        batch_size: int = 8,
        max_length: int = 8192,
        use_fp16: bool | None = None,
        cache_dir: str | Path | None = None,
        model_factory: Callable[..., object] | None = None,
    ) -> None:
        if not model_name.strip() or batch_size < 1 or max_length < 1:
            raise ValueError("BGE reranker configuration is invalid")
        if model_factory is None:
            model_source = _resolve_model_source(model_name, cache_dir=cache_dir)
            factory = _flag_embedding_factory("FlagReranker")
        else:
            model_source = model_name
            factory = model_factory
        kwargs: dict[str, object] = {
            "use_fp16": (_device_supports_fp16(device) if use_fp16 is None else use_fp16)
        }
        if cache_dir is not None:
            kwargs["cache_dir"] = str(Path(cache_dir))
        if device is not None:
            kwargs["devices"] = device
        self._model = cast(_RerankerModel, factory(model_source, **kwargs))
        self._model_name = model_name.strip()
        self._batch_size = batch_size
        self._max_length = max_length
        self._lock = asyncio.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    async def rerank(
        self, query: str, candidates: Sequence[RerankItem], *, top_k: int
    ) -> tuple[RerankItem, ...]:
        if not query.strip() or top_k < 1:
            raise ValueError("BGE reranker request is invalid")
        if not candidates:
            return ()
        pairs = [[query, item.text] for item in candidates]
        async with self._lock:
            raw_scores = await asyncio.to_thread(
                self._model.compute_score,
                pairs,
                batch_size=self._batch_size,
                max_length=self._max_length,
                normalize=True,
            )
        scores: tuple[float, ...]
        if isinstance(raw_scores, Real):
            scores = (float(raw_scores),)
        else:
            scores = tuple(float(value) for value in raw_scores)
        if len(scores) != len(candidates) or any(not math.isfinite(value) for value in scores):
            raise RuntimeError("BGE reranker returned invalid scores")
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda item: (-item[1], item[0].chunk_id),
        )
        return tuple(
            RerankItem(chunk_id=item.chunk_id, text=item.text, score=score)
            for item, score in ranked[:top_k]
        )


def _normalized_vector(value: object, dimension: int) -> tuple[float, ...]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        raise RuntimeError("BGE model returned a malformed vector")
    try:
        vector = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        raise RuntimeError("BGE model returned a malformed vector") from None
    if len(vector) != dimension or any(not math.isfinite(item) for item in vector):
        raise RuntimeError("BGE model returned an invalid vector dimension")
    norm = math.sqrt(sum(item * item for item in vector))
    if norm == 0:
        raise RuntimeError("BGE model returned a zero vector")
    return tuple(item / norm for item in vector)


def _flag_embedding_factory(name: str) -> Callable[..., object]:
    try:
        module = importlib.import_module("FlagEmbedding")
        factory = getattr(module, name)
    except (ImportError, AttributeError):
        raise RuntimeError(
            "BGE support is not installed; run 'uv sync --extra retrieval --extra bge'"
        ) from None
    return cast(Callable[..., object], factory)


def _resolve_model_source(model_name: str, *, cache_dir: str | Path | None) -> str:
    """Download only inference files, excluding large alternate ONNX assets."""

    local = Path(model_name).expanduser()
    if local.exists():
        return str(local.resolve(strict=True))
    if cache_dir is None:
        return model_name
    # The native xet transfer path can stall behind common Windows loopback
    # proxies. Users can explicitly set this to "0" to opt back in.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise RuntimeError(
            "BGE support is not installed; run 'uv sync --extra retrieval --extra bge'"
        ) from None
    snapshot: str = snapshot_download(
        repo_id=model_name,
        cache_dir=str(Path(cache_dir)),
        allow_patterns=[
            "config.json",
            "model.safetensors",
            "pytorch_model.bin",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "sentencepiece.bpe.model",
            "colbert_linear.pt",
            "sparse_linear.pt",
        ],
    )
    return snapshot


def _device_supports_fp16(device: str | None) -> bool:
    return device is not None and device.casefold().startswith(("cuda", "mps", "xpu"))


__all__ = ["BgeM3Embedding", "BgeReranker"]
