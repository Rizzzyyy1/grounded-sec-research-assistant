"""BM25 index: finance-aware tokenisation, ranking, pre-filtering, persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from finsight.core.exceptions import IndexingError
from finsight.core.filters import RetrievalFilters
from finsight.core.schemas import ChunkType
from finsight.indexing.sparse_index import SparseIndex, tokenize

from tests.unit.conftest import ChunkFactory  # isort: skip

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Net sales were $391,035 million.", ["net", "sales", "$391,035", "million"]),
        ("Gross margin was 46.2% in 2024", ["gross", "margin", "46.2%", "2024"]),
        ("our 10-K and the 10-Q filings", ["our", "10-k", "10-q", "filings"]),
        (
            "Risk factors: supply-chain disruption",
            ["risk", "factors", "supply-chain", "disruption"],
        ),
        ("The company's R&D", ["company's", "r&d"]),
    ],
)
def test_tokenizer_keeps_finance_tokens_intact(text: str, expected: list[str]) -> None:
    assert tokenize(text) == expected


def test_tokenizer_drops_stopwords_and_edge_punctuation() -> None:
    assert tokenize("The value of the assets, is high.") == ["value", "assets", "high"]
    assert tokenize("") == []


def build(make_chunk: ChunkFactory) -> tuple[SparseIndex, dict[str, str]]:
    chunks = [
        make_chunk("Net sales were $391,035 million in fiscal 2024", ticker="AAPL", item="7"),
        make_chunk("Supply chain disruption may harm our business", ticker="AAPL", item="1A"),
        make_chunk("Supply chain risk in cloud data centers", ticker="MSFT", item="1A"),
        make_chunk("| Net sales | $391,035 | $383,285 |", ticker="AAPL", item="8", kind=ChunkType.TABLE),
        make_chunk("Legal proceedings are described in Note 12", ticker="XOM", item="3", year=2023),
    ]  # fmt: skip
    index = SparseIndex()
    index.build(chunks)
    return index, {c.text: c.id for c in chunks}


def test_exact_number_query_finds_the_chunks_containing_it(make_chunk: ChunkFactory) -> None:
    index, ids = build(make_chunk)
    hits = index.search("$391,035", 5)
    assert {h.chunk_id for h in hits} == {
        ids["Net sales were $391,035 million in fiscal 2024"],
        ids["| Net sales | $391,035 | $383,285 |"],
    }


def test_ranking_prefers_more_query_terms(make_chunk: ChunkFactory) -> None:
    index, ids = build(make_chunk)
    hits = index.search("supply chain disruption business", 3)
    assert hits[0].chunk_id == ids["Supply chain disruption may harm our business"]
    assert all(h.score > 0 for h in hits)


def test_header_terms_are_searchable(make_chunk: ChunkFactory) -> None:
    """The contextual header is part of the indexed text, so a ticker query works lexically."""
    index, ids = build(make_chunk)
    assert index.search("MSFT", 3)[0].chunk_id == ids["Supply chain risk in cloud data centers"]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (RetrievalFilters(tickers=("MSFT",)), {"Supply chain risk in cloud data centers"}),
        (RetrievalFilters(items=("1A",)), {"Supply chain disruption may harm our business",
                                          "Supply chain risk in cloud data centers"}),
        (RetrievalFilters(tickers=("AAPL",), chunk_types=(ChunkType.TABLE,)), {"| Net sales | $391,035 | $383,285 |"}),
    ],
)  # fmt: skip
def test_filters_are_true_prefilters(
    make_chunk: ChunkFactory, filters: RetrievalFilters, expected: set[str]
) -> None:
    index, ids = build(make_chunk)
    hits = index.search("supply chain net sales", 10, filters)
    assert {h.chunk_id for h in hits} == {ids[t] for t in expected}


def test_no_match_empty_query_and_impossible_filter_return_nothing(
    make_chunk: ChunkFactory,
) -> None:
    index, _ = build(make_chunk)
    assert index.search("zzzqqq", 5) == []
    assert index.search("the of and", 5) == []  # only stopwords
    assert index.search("supply", 5, RetrievalFilters(tickers=("ZZZZ",))) == []
    assert SparseIndex().search("anything", 5) == []


def test_k_is_respected(make_chunk: ChunkFactory) -> None:
    index, _ = build(make_chunk)
    assert len(index.search("supply chain net sales legal", 2)) == 2


def test_save_and_load_roundtrip(tmp_path: Path, make_chunk: ChunkFactory) -> None:
    chunks = [make_chunk("alpha beta gamma"), make_chunk("delta epsilon gamma", ticker="MSFT")]
    index = SparseIndex()
    index.build(chunks)
    index.save(tmp_path / "bm25")
    loaded = SparseIndex.load(tmp_path / "bm25", {c.id: c for c in chunks})
    assert len(loaded) == 2
    assert [h.chunk_id for h in loaded.search("epsilon", 3)] == [chunks[1].id]
    assert [h.chunk_id for h in loaded.search("gamma", 3, RetrievalFilters(tickers=("MSFT",)))] == [
        chunks[1].id
    ]


def test_load_detects_a_stale_index_and_a_missing_one(
    tmp_path: Path, make_chunk: ChunkFactory
) -> None:
    chunks = [make_chunk("alpha"), make_chunk("beta")]
    index = SparseIndex()
    index.build(chunks)
    index.save(tmp_path / "bm25")
    with pytest.raises(IndexingError, match="not in the corpus"):
        SparseIndex.load(tmp_path / "bm25", {chunks[0].id: chunks[0]})  # corpus changed
    with pytest.raises(IndexingError, match="finsight index"):
        SparseIndex.load(tmp_path / "nope", {})
