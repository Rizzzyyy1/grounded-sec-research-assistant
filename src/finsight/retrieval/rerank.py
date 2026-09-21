"""Cross-encoder reranking of the fused candidate list.

A bi-encoder (the dense retriever) embeds query and passage *independently*; a cross-encoder reads
them *together*, which is far more precise but too slow to run over the whole corpus - hence a
two-stage design: cheap recall first, expensive precision on the top-N. Reranking is optional and
ablated (docs/EVALUATION.md A1/A6): it ships as the default only if it earns its latency.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from finsight.config.settings import RetrievalSettings
from finsight.core.schemas import Chunk
from finsight.indexing.sparse_index import tokenize


class Reranker(Protocol):
    name: str

    def score(self, query: str, chunks: Sequence[Chunk]) -> list[float]:
        """One relevance score per chunk (higher = more relevant), in input order."""
        ...


class FastEmbedReranker:
    def __init__(self, model_name: str) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # noqa: PLC0415

        self.name = model_name
        self._model = TextCrossEncoder(model_name)

    def score(self, query: str, chunks: Sequence[Chunk]) -> list[float]:
        if not chunks:
            return []
        return [float(s) for s in self._model.rerank(query, [c.indexed_text for c in chunks])]


class LexicalReranker:
    """Deterministic fake for tests: fraction of query terms present in the chunk text."""

    name = "lexical-overlap"

    def score(self, query: str, chunks: Sequence[Chunk]) -> list[float]:
        terms = set(tokenize(query))
        if not terms:
            return [0.0] * len(chunks)
        return [len(terms & set(tokenize(c.text))) / len(terms) for c in chunks]


def make_reranker(settings: RetrievalSettings) -> Reranker | None:
    return FastEmbedReranker(settings.rerank_model) if settings.rerank else None
