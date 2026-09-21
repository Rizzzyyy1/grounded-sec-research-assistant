"""Embedding models behind a small protocol.

* :class:`FastEmbedder` - real BGE model via ONNX Runtime (no PyTorch). Inputs are sorted by
  length before batching so padding is minimal; output order is restored.
* :class:`HashingEmbedder` - deterministic signed feature hashing of word tokens. It has no
  model download and no randomness, so unit tests and CI are hermetic, yet lexical overlap still
  yields similarity, which is enough to test retrieval plumbing meaningfully.

Vectors are float32 and L2-normalised, so cosine similarity is a dot product.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from finsight.config.settings import EmbeddingSettings

Vectors = NDArray[np.float32]
_WORD = re.compile(r"[a-z0-9$%][a-z0-9$%.,'&-]*", re.IGNORECASE)


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(
        self, texts: Sequence[str], on_batch: Callable[[int], None] | None = None
    ) -> Vectors: ...

    def embed_query(self, text: str) -> Vectors: ...


def _normalise(matrix: Vectors) -> Vectors:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return np.asarray(matrix / np.maximum(norms, 1e-12), dtype=np.float32)


class HashingEmbedder:
    """Deterministic bag-of-words embedder (signed feature hashing, log-tf weighting)."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _vector(self, text: str) -> Vectors:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token, count in Counter(w.lower() for w in _WORD.findall(text)).items():
            digest = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
            sign = 1.0 if (digest >> 32) & 1 else -1.0
            vec[digest % self.dim] += sign * (1.0 + math.log(count))
        return vec

    def embed_documents(
        self, texts: Sequence[str], on_batch: Callable[[int], None] | None = None
    ) -> Vectors:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        out = _normalise(np.stack([self._vector(t) for t in texts]))
        if on_batch:
            on_batch(len(texts))
        return out

    def embed_query(self, text: str) -> Vectors:
        return self._vector(text) / max(float(np.linalg.norm(self._vector(text))), 1e-12)


class FastEmbedder:
    """BGE (or any fastembed-supported) model on ONNX Runtime."""

    def __init__(self, settings: EmbeddingSettings) -> None:
        from fastembed import TextEmbedding  # noqa: PLC0415 - heavy import, only when used

        self._settings = settings
        self._model = TextEmbedding(settings.model_name)
        self.name = settings.model_name
        self.dim = int(next(iter(self._model.embed(["dimension probe"]))).shape[0])

    def embed_documents(
        self, texts: Sequence[str], on_batch: Callable[[int], None] | None = None
    ) -> Vectors:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        order = np.argsort(
            [len(t) for t in texts], kind="stable"
        )  # similar lengths -> less padding
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        size = self._settings.batch_size
        for start in range(0, len(texts), size):
            idx = order[start : start + size]
            batch = [texts[i] for i in idx]
            out[idx] = np.asarray(
                list(self._model.embed(batch, batch_size=len(batch))), dtype=np.float32
            )
            if on_batch:
                on_batch(len(batch))
        return _normalise(out)

    def embed_query(self, text: str) -> Vectors:
        raw = list(self._model.embed([self._settings.query_prefix + text]))
        vector = np.asarray(raw[0], dtype=np.float32)
        return vector / max(float(np.linalg.norm(vector)), 1e-12)


def make_embedder(settings: EmbeddingSettings) -> Embedder:
    if settings.backend == "hashing":
        return HashingEmbedder()
    return FastEmbedder(settings)
