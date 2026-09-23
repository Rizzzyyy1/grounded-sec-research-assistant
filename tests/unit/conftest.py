"""Shared factories for unit tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from finsight.core.schemas import Chunk, ChunkMetadata, ChunkType, FiscalPeriod, FormType

ChunkFactory = Callable[..., Chunk]


@pytest.fixture
def make_chunk() -> ChunkFactory:
    counter = {"n": 0}

    def factory(
        text: str,
        *,
        ticker: str = "AAPL",
        year: int = 2024,
        item: str = "1A",
        form: FormType = FormType.TEN_K,
        period: FiscalPeriod = FiscalPeriod.FY,
        kind: ChunkType = ChunkType.TEXT,
        accession: str | None = None,
        tokens: int | None = None,
        header: bool = True,
    ) -> Chunk:
        counter["n"] += 1
        # an accession must look like 0000000000-00-000000
        acc = accession or f"{counter['n']:010d}-{year % 100:02d}-000001"
        meta = ChunkMetadata(
            ticker=ticker, cik="0000000001", company=f"{ticker} Inc.", form=form,
            fiscal_year=year, fiscal_period=period, accession=acc, item=item,
            item_title="Risk Factors", chunk_type=kind, filed=date(year, 11, 1),
            source_url=f"https://example.com/{acc}", ordinal=counter["n"],
            token_count=tokens if tokens is not None else len(text.split()),
        )  # fmt: skip
        chunk = Chunk(id=Chunk.make_id(acc, item, counter["n"], text), text=text, metadata=meta)
        if header:
            return chunk.model_copy(
                update={"embed_text": f"{ticker} | {form.value} FY{year} | Item {item}\n{text}"}
            )
        return chunk

    return factory


# ---------------------------------------------------------------- shared agent/API world

from pathlib import Path  # noqa: E402

from finsight.agent.tools import AgentContext  # noqa: E402
from finsight.config.settings import RetrievalSettings  # noqa: E402
from finsight.config.universe import Universe, load_universe  # noqa: E402
from finsight.core.schemas import FilingRef, FinancialFact  # noqa: E402
from finsight.indexing.embeddings import HashingEmbedder  # noqa: E402
from finsight.indexing.sparse_index import SparseIndex  # noqa: E402
from finsight.indexing.vector_store import InMemoryVectorStore  # noqa: E402
from finsight.ingestion.xbrl.facts import ParsedFacts  # noqa: E402
from finsight.ingestion.xbrl.store import FactStore  # noqa: E402
from finsight.retrieval.dense import DenseRetriever  # noqa: E402
from finsight.retrieval.query_analysis import QueryAnalyzer  # noqa: E402
from finsight.retrieval.retriever import Retriever  # noqa: E402
from finsight.retrieval.sparse import SparseRetriever  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# AAPL and MSFT have full data; JPM is a bank without gross profit.
VALUES = {
    "AAPL": {"revenue": 391_035e6, "gross_profit": 180_683e6, "net_income": 93_736e6,
             "shareholders_equity": 56_950e6, "total_assets": 364_980e6, "operating_income": 123_216e6},
    "MSFT": {"revenue": 245_122e6, "gross_profit": 171_008e6, "net_income": 88_136e6,
             "shareholders_equity": 268_477e6, "total_assets": 512_163e6, "operating_income": 109_433e6},
    "JPM": {"revenue": 177_556e6, "net_income": 58_471e6, "shareholders_equity": 344_758e6,
            "total_assets": 4_002_814e6},
}  # fmt: skip
INSTANT = {"shareholders_equity", "total_assets"}
_TICKER_NUM = {"AAPL": 1, "MSFT": 2, "JPM": 3}


def _accession(ticker: str, year: int) -> str:
    """One accession per (ticker, year), like a real filing - AAPL FY2024 keeps the literal
    value ``0000000001-24-000001`` several tests assert on."""
    return f"{_TICKER_NUM[ticker]:010d}-{year % 100:02d}-000001"


def _facts(ticker: str) -> ParsedFacts:
    facts = []
    for year in (2022, 2023, 2024):
        scale = {2022: 0.85, 2023: 0.93, 2024: 1.0}[year]
        for metric, value in VALUES[ticker].items():
            instant = metric in INSTANT
            facts.append(FinancialFact(
                ticker=ticker, cik="0000000001", metric=metric, tag=f"tag:{metric}", value=value * scale,
                unit="USD", period_type="instant" if instant else "duration",
                start=None if instant else date(year - 1, 10, 1), end=date(year, 9, 28),
                fiscal_year=year, fiscal_period=FiscalPeriod.FY, form=FormType.TEN_K,
                filed=date(year, 11, 1), accession=_accession(ticker, year)))  # fmt: skip
    return ParsedFacts(facts, [])


@pytest.fixture
def universe() -> Universe:
    return load_universe(ROOT / "configs" / "universe.yaml")


@pytest.fixture
def ctx(make_chunk: ChunkFactory, universe: Universe) -> AgentContext:  # type: ignore[misc]
    store = FactStore()
    for t in VALUES:
        store.replace_company_facts(t, _facts(t))
        for year in (2022, 2023, 2024):
            acc = _accession(t, year)
            store.upsert_filing(FilingRef(
                cik="0000000001", ticker=t, company=f"{t} Inc.", form=FormType.TEN_K, accession=acc,
                filed=date(year, 11, 1), period_of_report=date(year, 9, 28), fiscal_year=year,
                fiscal_period=FiscalPeriod.FY, primary_doc="doc.htm",
                url=f"https://example.com/{acc}",
            ))  # fmt: skip
    corpus = [
        make_chunk("Apple depends on outsourcing partners in China mainland for manufacturing.", ticker="AAPL", year=2024, item="1A"),
        make_chunk("Apple faces new regulation of artificial intelligence that could increase compliance costs.", ticker="AAPL", year=2024, item="1A"),
        make_chunk("Apple depends on outsourcing partners in China mainland for manufacturing.", ticker="AAPL", year=2023, item="1A"),
        make_chunk("Apple is exposed to changes in tax law that could raise its effective tax rate.", ticker="AAPL", year=2023, item="1A"),
        make_chunk("Microsoft cloud revenue grew strongly driven by Azure demand.", ticker="MSFT", year=2024, item="7"),
    ]  # fmt: skip
    emb, vec, sparse = HashingEmbedder(512), InMemoryVectorStore(), SparseIndex()
    vec.upsert(corpus, emb.embed_documents([c.indexed_text for c in corpus]))
    sparse.build(corpus)
    retriever = Retriever(
        {c.id: c for c in corpus}, RetrievalSettings(rerank=False, final_k=4),
        dense=DenseRetriever(emb, vec), sparse=SparseRetriever(sparse),
        analyzer=QueryAnalyzer(universe),
    )  # fmt: skip
    yield AgentContext(facts=store, retriever=retriever, universe=universe)  # type: ignore[misc]
    store.close()
