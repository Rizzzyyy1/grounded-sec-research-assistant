"""Dense retriever: embed the query, search the vector store."""

from __future__ import annotations

from finsight.core.filters import RetrievalFilters
from finsight.indexing.embeddings import Embedder
from finsight.indexing.vector_store import Hit, VectorStore


class DenseRetriever:
    def __init__(self, embedder: Embedder, store: VectorStore) -> None:
        self._embedder = embedder
        self._store = store

    def search(self, query: str, k: int, filters: RetrievalFilters | None = None) -> list[Hit]:
        return self._store.search(self._embedder.embed_query(query), k, filters)
