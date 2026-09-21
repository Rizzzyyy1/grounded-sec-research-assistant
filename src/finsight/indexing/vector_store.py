"""Vector stores: in-memory (tests) and Qdrant (embedded locally, or a server).

Both implement :class:`VectorStore` and return only ``(chunk_id, score)``; the text lives in one
chunk catalogue, so an index never duplicates the corpus. Filters are applied inside the store
(pre-filtering). Qdrant point ids must be UUIDs or ints, so they are derived from the chunk id
with UUIDv5 (deterministic, hence idempotent upserts) and the real id is kept in the payload.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from finsight.core.filters import RetrievalFilters
from finsight.core.schemas import Chunk
from finsight.indexing.embeddings import Vectors

_NAMESPACE = uuid.UUID("6f1a1f0e-5d0c-4f5f-9d5e-0f1a5b9c7f00")
_KEYWORD_FIELDS = ("ticker", "form", "item", "chunk_type", "fiscal_period")
_INT_FIELDS = ("fiscal_year",)


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    score: float


class VectorStore(Protocol):
    def upsert(self, chunks: Sequence[Chunk], vectors: Vectors) -> None: ...

    def search(
        self, vector: Vectors, k: int, filters: RetrievalFilters | None = None
    ) -> list[Hit]: ...

    def indexed_ids(self) -> set[str]: ...

    def count(self) -> int: ...


def _payload(chunk: Chunk) -> dict[str, Any]:
    m = chunk.metadata
    return {
        "chunk_id": chunk.id, "ticker": m.ticker, "form": m.form.value, "item": m.item,
        "fiscal_year": m.fiscal_year, "fiscal_period": m.fiscal_period.value,
        "chunk_type": m.chunk_type.value,
    }  # fmt: skip


class InMemoryVectorStore:
    """Exact cosine search over a numpy matrix. Used in tests and for tiny corpora."""

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._chunks: list[Chunk] = []
        self._matrix: Vectors = np.zeros((0, 0), dtype=np.float32)

    def upsert(self, chunks: Sequence[Chunk], vectors: Vectors) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        position = {cid: i for i, cid in enumerate(self._ids)}
        for chunk, vector in zip(chunks, vectors, strict=True):
            if chunk.id in position:
                self._matrix[position[chunk.id]] = vector
                continue
            if self._matrix.size == 0:
                self._matrix = np.zeros((0, vector.shape[0]), dtype=np.float32)
            position[chunk.id] = len(self._ids)
            self._ids.append(chunk.id)
            self._chunks.append(chunk)
            self._matrix = np.vstack([self._matrix, vector[None, :]])

    def search(self, vector: Vectors, k: int, filters: RetrievalFilters | None = None) -> list[Hit]:
        if not self._ids:
            return []
        scores = self._matrix @ vector
        if filters and not filters.is_empty:
            allowed = np.array([filters.matches(c.metadata) for c in self._chunks])
            scores = np.where(allowed, scores, -np.inf)
        top = np.argsort(-scores, kind="stable")[:k]
        return [Hit(self._ids[i], float(scores[i])) for i in top if np.isfinite(scores[i])]

    def indexed_ids(self) -> set[str]:
        return set(self._ids)

    def count(self) -> int:
        return len(self._ids)


class QdrantVectorStore:
    """Qdrant collection (cosine). ``path`` = embedded local mode; ``url`` = server."""

    def __init__(
        self,
        *,
        dim: int,
        collection: str = "filings",
        path: Path | None = None,
        url: str | None = None,
    ) -> None:
        from qdrant_client import QdrantClient, models  # noqa: PLC0415

        self._models = models
        self._collection = collection
        if url:
            self._client = QdrantClient(url=url)
        elif path is not None:
            path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(path))
        else:
            raise ValueError("provide either path (embedded) or url (server)")
        if not self._client.collection_exists(collection):
            self._client.create_collection(
                collection,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )
            if url:  # payload indexes only exist on a server; local mode filters by scanning
                for field in _KEYWORD_FIELDS:
                    self._client.create_payload_index(
                        collection, field, models.PayloadSchemaType.KEYWORD
                    )
                for field in _INT_FIELDS:
                    self._client.create_payload_index(
                        collection, field, models.PayloadSchemaType.INTEGER
                    )

    def _filter(self, filters: RetrievalFilters | None) -> Any:
        if filters is None or filters.is_empty:
            return None
        m = self._models
        must: list[Any] = []
        for field, values in (
            ("ticker", filters.tickers),
            ("form", [f.value for f in filters.forms]),
            ("item", filters.items),
            ("chunk_type", [c.value for c in filters.chunk_types]),
            ("fiscal_period", [p.value for p in filters.fiscal_periods]),
            ("fiscal_year", filters.fiscal_years),
        ):
            if values:
                must.append(m.FieldCondition(key=field, match=m.MatchAny(any=list(values))))
        return m.Filter(must=must)

    def upsert(self, chunks: Sequence[Chunk], vectors: Vectors) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        points = [
            self._models.PointStruct(
                id=str(uuid.uuid5(_NAMESPACE, c.id)), vector=v.tolist(), payload=_payload(c)
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        self._client.upsert(self._collection, points=points)

    def search(self, vector: Vectors, k: int, filters: RetrievalFilters | None = None) -> list[Hit]:
        response = self._client.query_points(
            self._collection,
            query=vector.tolist(),
            limit=k,
            query_filter=self._filter(filters),
            with_payload=["chunk_id"],
        )
        return [
            Hit(str(p.payload["chunk_id"]), float(p.score)) for p in response.points if p.payload
        ]

    def indexed_ids(self) -> set[str]:
        ids: set[str] = set()
        offset = None
        while True:
            points, offset = self._client.scroll(
                self._collection,
                limit=2048,
                offset=offset,
                with_payload=["chunk_id"],
                with_vectors=False,
            )
            ids.update(str(p.payload["chunk_id"]) for p in points if p.payload)
            if offset is None:
                return ids

    def count(self) -> int:
        return int(self._client.count(self._collection, exact=True).count)

    def close(self) -> None:
        self._client.close()
