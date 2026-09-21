"""Both vector stores must behave identically (same contract, same filters)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from tests.unit.conftest import ChunkFactory  # type: ignore[import-not-found]

from finsight.core.filters import RetrievalFilters
from finsight.core.schemas import Chunk, ChunkType, FiscalPeriod, FormType
from finsight.indexing.embeddings import HashingEmbedder
from finsight.indexing.vector_store import InMemoryVectorStore, QdrantVectorStore, VectorStore

pytestmark = pytest.mark.unit


@pytest.fixture(params=["memory", "qdrant"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[VectorStore]:
    if request.param == "memory":
        yield InMemoryVectorStore()
    else:
        qdrant = QdrantVectorStore(dim=256, path=tmp_path / "q")
        yield qdrant
        qdrant.close()


def load(store: VectorStore, make_chunk: ChunkFactory) -> dict[str, str]:
    chunks = corpus(make_chunk)
    store.upsert(chunks, HashingEmbedder(256).embed_documents([c.indexed_text for c in chunks]))
    return {c.text: c.id for c in chunks}


def corpus(make_chunk: ChunkFactory) -> list[Chunk]:
    specs = [
        ("supply chain risk in asia", dict(ticker="AAPL", year=2024, item="1A")),
        ("supply chain costs rose", dict(ticker="AAPL", year=2023, item="7")),
        ("supply chain risk factors", dict(ticker="MSFT", year=2024, item="1A")),
        ("revenue by segment table", dict(ticker="AAPL", year=2024, item="8", kind=ChunkType.TABLE)),
        ("quarterly supply chain update", dict(ticker="AAPL", year=2024, item="7", form=FormType.TEN_Q,
                                               period=FiscalPeriod.Q2)),
    ]  # fmt: skip
    return [make_chunk(t, **kw) for t, kw in specs]


def q(text: str) -> np.ndarray:  # type: ignore[type-arg]
    return HashingEmbedder(256).embed_query(text)


def test_search_ranks_by_similarity_and_reports_scores(
    store: VectorStore, make_chunk: ChunkFactory
) -> None:
    ids = load(store, make_chunk)
    hits = store.search(q("supply chain risk in asia"), 3)
    assert hits[0].chunk_id == ids["supply chain risk in asia"]
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert len(hits) == 3


def test_upsert_is_idempotent_and_indexed_ids_roundtrip(
    store: VectorStore, make_chunk: ChunkFactory
) -> None:
    chunks = corpus(make_chunk)
    vectors = HashingEmbedder(256).embed_documents([c.indexed_text for c in chunks])
    store.upsert(chunks, vectors)
    store.upsert(chunks, vectors)  # the very same chunks again
    assert store.count() == 5
    assert store.indexed_ids() == {c.id for c in chunks}


@pytest.mark.parametrize(
    ("filters", "expected_texts"),
    [
        (RetrievalFilters(tickers=("MSFT",)), {"supply chain risk factors"}),
        (RetrievalFilters(fiscal_years=(2023,)), {"supply chain costs rose"}),
        (RetrievalFilters(items=("8",)), {"revenue by segment table"}),
        (RetrievalFilters(chunk_types=(ChunkType.TABLE,)), {"revenue by segment table"}),
        (RetrievalFilters(forms=(FormType.TEN_Q,)), {"quarterly supply chain update"}),
        (RetrievalFilters(fiscal_periods=(FiscalPeriod.Q2,)), {"quarterly supply chain update"}),
        (
            RetrievalFilters(tickers=("AAPL",), fiscal_years=(2024,), forms=(FormType.TEN_K,)),
            {"supply chain risk in asia", "revenue by segment table"},
        ),
        (RetrievalFilters(tickers=("AAPL", "MSFT"), items=("1A",)),
         {"supply chain risk in asia", "supply chain risk factors"}),
    ],
)  # fmt: skip
def test_filters_are_prefilters_and_combine_with_and(
    store: VectorStore,
    make_chunk: ChunkFactory,
    filters: RetrievalFilters,
    expected_texts: set[str],
) -> None:
    ids = load(store, make_chunk)
    hits = store.search(q("supply chain risk"), 10, filters)  # k far above the match count
    assert {h.chunk_id for h in hits} == {ids[t] for t in expected_texts}


def test_filter_matching_nothing_returns_empty_not_an_error(
    store: VectorStore, make_chunk: ChunkFactory
) -> None:
    load(store, make_chunk)
    assert store.search(q("anything"), 5, RetrievalFilters(tickers=("ZZZZ",))) == []


def test_empty_filters_are_a_noop(store: VectorStore, make_chunk: ChunkFactory) -> None:
    load(store, make_chunk)
    assert len(store.search(q("supply"), 10, RetrievalFilters())) == 5
    assert len(store.search(q("supply"), 10, None)) == 5


def test_length_mismatch_is_rejected(store: VectorStore, make_chunk: ChunkFactory) -> None:
    chunk = make_chunk("x y z")
    with pytest.raises(ValueError, match="same length"):
        store.upsert([chunk], np.zeros((2, 256), dtype=np.float32))


def test_empty_store_search_returns_nothing(store: VectorStore) -> None:
    assert store.search(q("anything"), 5) == []


def test_qdrant_persists_across_reopen(tmp_path: Path, make_chunk: ChunkFactory) -> None:
    first = QdrantVectorStore(dim=256, path=tmp_path / "q")
    ids = load(first, make_chunk)
    first.close()
    reopened = QdrantVectorStore(dim=256, path=tmp_path / "q")
    try:
        assert reopened.indexed_ids() == set(ids.values())
    finally:
        reopened.close()


def test_qdrant_requires_a_location() -> None:
    with pytest.raises(ValueError, match="path"):
        QdrantVectorStore(dim=8)
