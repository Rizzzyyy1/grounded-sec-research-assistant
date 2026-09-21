"""Regenerate the retrieval-regression fixture from the real corpus.

    .venv/bin/python scripts/make_regression_fixture.py

Writes ``tests/fixtures/mini_corpus.parquet`` (a few hundred real 10-K passages from four filings)
and ``tests/fixtures/mini_gold.jsonl`` (section-level questions over them). The CI test
``tests/integration/test_retrieval_regression.py`` evaluates hybrid retrieval over this fixture
with the deterministic hashing embedder and fails if quality drops below the committed baseline.
Re-run this script only when the chunker changes on purpose, then review the baseline diff.
"""

from __future__ import annotations

import random
from pathlib import Path

from finsight.config.universe import load_universe
from finsight.core.schemas import QueryType
from finsight.evaluation.datasets import Expected, GoldExample, GoldSource, write_gold
from finsight.evaluation.gold_builder import _SECTION_TEMPLATES, short_name
from finsight.processing.pipeline import read_chunks, write_chunks

ROOT = Path(__file__).resolve().parents[1]
FILINGS = [("AAPL", 2024), ("MSFT", 2024), ("XOM", 2023), ("WMT", 2024)]
ITEMS = ("1", "1A", "2", "3", "7", "7A")
PER_SECTION = 14


def main() -> None:
    rng = random.Random(11)
    universe = load_universe(ROOT / "configs" / "universe.yaml")
    chunks = read_chunks(ROOT / "data" / "processed" / "chunks.parquet")
    picked = []
    for ticker, year in FILINGS:
        for item in ITEMS:
            pool = [
                c
                for c in chunks
                if (c.metadata.ticker, c.metadata.fiscal_year, c.metadata.item)
                == (ticker, year, item)
                and c.metadata.chunk_type.value == "text"
            ]
            picked += rng.sample(pool, k=min(PER_SECTION, len(pool)))
    write_chunks(picked, ROOT / "tests" / "fixtures" / "mini_corpus.parquet")

    have = {(c.metadata.ticker, c.metadata.fiscal_year, c.metadata.item) for c in picked}
    examples = []
    for ticker, year in FILINGS:
        name = short_name(universe.company(ticker))
        for template, item in _SECTION_TEMPLATES:
            if (ticker, year, item) in have:
                examples.append(
                    GoldExample(
                        id=f"fx-{ticker}-{item}",
                        split="dev",
                        type=QueryType.QUALITATIVE,
                        question=template.format(name=name, year=year),
                        expected=Expected(),
                        gold_sources=(GoldSource(ticker=ticker, fiscal_year=year, item=item),),
                        provenance="template",
                    )
                )
    write_gold(examples, ROOT / "tests" / "fixtures" / "mini_gold.jsonl")
    print(f"{len(picked)} chunks, {len(examples)} questions")


if __name__ == "__main__":
    main()
