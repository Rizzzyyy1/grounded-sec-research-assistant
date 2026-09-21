"""Sparse retriever: BM25 search over the sparse index."""

from __future__ import annotations

from finsight.core.filters import RetrievalFilters
from finsight.indexing.sparse_index import SparseIndex
from finsight.indexing.vector_store import Hit


class SparseRetriever:
    def __init__(self, index: SparseIndex) -> None:
        self._index = index

    def search(self, query: str, k: int, filters: RetrievalFilters | None = None) -> list[Hit]:
        return self._index.search(query, k, filters)
