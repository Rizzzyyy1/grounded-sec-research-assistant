"""Chunker invariants: budget, coverage, boundaries, tables, determinism."""

from __future__ import annotations

import re
from datetime import date
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finsight.config.settings import ChunkingSettings
from finsight.core.schemas import ChunkType, FilingRef, FiscalPeriod, FormType
from finsight.processing.chunking import (
    chunk_section,
    count_tokens,
    split_sentences,
)
from finsight.processing.enrichment import add_context_header, context_header
from finsight.processing.html_parser import Block
from finsight.processing.sections import SectionBlocks

pytestmark = pytest.mark.unit

FILING = FilingRef(
    cik="0000320193", ticker="AAPL", company="Apple Inc.", form=FormType.TEN_K,
    accession="0000320193-24-000123", filed=date(2024, 11, 1), period_of_report=date(2024, 9, 28),
    fiscal_year=2024, fiscal_period=FiscalPeriod.FY, primary_doc="a.htm", url="https://x/a.htm",
)  # fmt: skip


def cfg(**kw: object) -> ChunkingSettings:
    base: dict[str, object] = {"target_tokens": 100, "min_tokens": 10, "overlap_ratio": 0.15}
    return ChunkingSettings(**{**base, **kw})  # type: ignore[arg-type]


def section(*blocks: Block, item: str = "1A", title: str = "Risk Factors") -> SectionBlocks:
    return SectionBlocks(item, title, tuple(blocks))


def para(n_sentences: int, words: int = 12, tag: str = "w") -> Block:
    sentences = [
        " ".join(f"{tag}{i}x{j}" for j in range(words)).capitalize() + "."
        for i in range(n_sentences)
    ]
    return Block("text", " ".join(sentences))


def table(*rows: tuple[str, ...]) -> Block:
    return Block("table", "\n".join("| " + " | ".join(r) + " |" for r in rows), tuple(rows))


def test_token_counting_is_deterministic_and_counts_punctuation() -> None:
    assert count_tokens("Net sales were $391,035 million.") == 9
    assert count_tokens("") == 0


def test_sentence_splitter() -> None:
    assert split_sentences("One. Two! Three? Four") == ["One.", "Two!", "Three?", "Four"]
    # A lowercase continuation is not a boundary ("approx. five", "e.g. this"):
    assert split_sentences("About approx. five items. Next one.") == [
        "About approx. five items.",
        "Next one.",
    ]


def test_small_section_is_one_chunk_with_full_metadata() -> None:
    (chunk,) = chunk_section(section(para(2)), FILING, cfg())
    m = chunk.metadata
    assert (m.ticker, m.item, m.item_title, m.fiscal_year, m.ordinal) == (
        "AAPL",
        "1A",
        "Risk Factors",
        2024,
        0,
    )
    assert m.accession == FILING.accession
    assert m.source_url == FILING.url
    assert m.token_count == count_tokens(chunk.text)


def test_empty_section_yields_no_chunks() -> None:
    assert chunk_section(section(), FILING, cfg()) == []


def test_long_section_is_split_within_budget() -> None:
    chunks = chunk_section(section(*[para(4) for _ in range(20)]), FILING, cfg())
    assert len(chunks) > 3
    assert all(c.metadata.token_count <= 100 + 10 for c in chunks)  # budget + min_tokens slack


def test_overlap_repeats_trailing_sentences_when_enabled() -> None:
    blocks = [para(3, tag=f"p{i}") for i in range(12)]
    with_overlap = chunk_section(section(*blocks), FILING, cfg(overlap_ratio=0.3))
    without = chunk_section(section(*blocks), FILING, cfg(overlap_ratio=0.0))

    def shares_sentence(chunks: list) -> int:  # type: ignore[type-arg]
        hits = 0
        for a, b in pairwise(chunks):
            last = split_sentences(a.text.replace("\n\n", " "))[-1]
            hits += last in b.text
        return hits

    assert shares_sentence(with_overlap) > 0
    assert shares_sentence(without) == 0


def test_oversized_paragraph_and_sentence_are_split_not_dropped() -> None:
    giant_sentence = " ".join(f"word{i}" for i in range(500)) + "."
    chunks = chunk_section(section(Block("text", giant_sentence)), FILING, cfg())
    assert len(chunks) >= 5
    assert all(c.metadata.token_count <= 110 for c in chunks)
    original = set(re.findall(r"word\d+", giant_sentence))
    assert original <= set(re.findall(r"word\d+", " ".join(c.text for c in chunks)))


def test_table_is_a_standalone_chunk_of_type_table() -> None:
    tbl = table(("Item", "2024", "2023"), ("Net sales", "391", "383"))
    chunks = chunk_section(section(para(6), tbl, para(6, tag="z")), FILING, cfg())
    kinds = [c.metadata.chunk_type for c in chunks]
    assert kinds == [ChunkType.TEXT, ChunkType.TABLE, ChunkType.TEXT]
    assert "| Net sales | 391 | 383 |" in chunks[1].text
    assert "|" not in chunks[0].text  # table markup never leaks into prose chunks


def test_short_paragraph_before_a_table_becomes_its_caption_without_duplication() -> None:
    caption = Block("text", "Consolidated Statements of Operations (in millions)")
    tbl = table(("Item", "2024"), ("Net sales", "391"))
    chunks = chunk_section(section(para(3), caption, tbl), FILING, cfg())
    table_chunk = next(c for c in chunks if c.metadata.chunk_type is ChunkType.TABLE)
    assert table_chunk.text.startswith("Consolidated Statements of Operations")
    assert sum("Consolidated Statements" in c.text for c in chunks) == 1


def test_large_table_is_split_by_rows_repeating_the_header() -> None:
    rows = [("Metric", "2024", "2023")] + [
        (f"Line {i}", str(i * 3), str(i * 2)) for i in range(120)
    ]
    chunks = chunk_section(section(table(*rows)), FILING, cfg())
    assert len(chunks) > 1
    assert all(c.text.splitlines()[0] == "| Metric | 2024 | 2023 |" for c in chunks)
    assert all(c.metadata.token_count <= 2 * 100 + 5 for c in chunks)
    rebuilt = {ln for c in chunks for ln in c.text.splitlines()}
    assert all(f"| Line {i} | {i * 3} | {i * 2} |" in rebuilt for i in range(120))


def test_tables_can_be_folded_into_prose_when_disabled() -> None:
    chunks = chunk_section(section(table(("a", "1"))), FILING, cfg(tables_as_chunks=False))
    assert all(c.metadata.chunk_type is ChunkType.TEXT for c in chunks)


def test_tiny_fragments_are_absorbed_not_stranded() -> None:
    blocks = [para(6), Block("text", "Note."), Block("text", "See below."), para(1, tag="t")]
    chunks = chunk_section(section(*blocks), FILING, cfg(min_tokens=15))
    assert all(c.metadata.token_count >= 15 for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert "Note." in joined
    assert "See below." in joined


def test_ids_are_deterministic_unique_and_content_sensitive() -> None:
    blocks = [para(4, tag=f"p{i}") for i in range(10)]
    a = chunk_section(section(*blocks), FILING, cfg())
    b = chunk_section(section(*blocks), FILING, cfg())
    assert [c.id for c in a] == [c.id for c in b]
    assert len({c.id for c in a}) == len(a)
    changed = chunk_section(section(*blocks[:-1], para(4, tag="CHANGED")), FILING, cfg())
    assert a[-1].id != changed[-1].id
    assert [c.metadata.ordinal for c in a] == list(range(len(a)))


def test_context_header_goes_on_embed_text_only() -> None:
    (chunk,) = chunk_section(section(para(2)), FILING, cfg())
    assert chunk.embed_text is not None
    assert chunk.embed_text.startswith("Apple Inc. (AAPL) | 10-K FY2024 | Item 1A - Risk Factors\n")
    assert chunk.embed_text.endswith(chunk.text)
    assert "AAPL" not in chunk.text  # the LLM-facing text is untouched


def test_context_header_can_be_disabled() -> None:
    (chunk,) = chunk_section(section(para(2)), FILING, cfg(context_header=False))
    assert chunk.embed_text is None
    assert chunk.indexed_text == chunk.text


def test_context_header_variants() -> None:
    (chunk,) = chunk_section(
        section(para(2), item="UNK", title=""), FILING, cfg(context_header=False)
    )
    assert context_header(chunk.metadata).endswith("| Document")
    table_chunk = chunk_section(section(table(("a", "1"))), FILING, cfg(context_header=False))[0]
    assert context_header(table_chunk.metadata).endswith("| Table")
    quarter = FILING.model_copy(update={"form": FormType.TEN_Q, "fiscal_period": FiscalPeriod.Q2})
    q_chunk = chunk_section(section(para(2)), quarter, cfg(context_header=False))[0]
    assert "10-Q FY2024 Q2" in context_header(q_chunk.metadata)
    assert add_context_header(q_chunk).embed_text is not None


@settings(max_examples=60, deadline=None)
@given(
    paragraphs=st.lists(st.tuples(st.integers(1, 12), st.integers(1, 30)), min_size=1, max_size=25),
    target=st.integers(50, 200),
    overlap=st.sampled_from([0.0, 0.15, 0.3]),
)
def test_property_budget_and_no_content_loss(
    paragraphs: list[tuple[int, int]], target: int, overlap: float
) -> None:
    blocks = [para(n, w, tag=f"p{i}") for i, (n, w) in enumerate(paragraphs)]
    config = ChunkingSettings(target_tokens=target, min_tokens=8, overlap_ratio=overlap)
    chunks = chunk_section(section(*blocks), FILING, config)
    # 1. budget: never above target + a few slivers' slack
    assert all(c.metadata.token_count <= target + 3 * 8 for c in chunks)
    # 2. no content loss: every input word survives somewhere in the output
    source_words = set(re.findall(r"p\d+x\d+x\d+", " ".join(b.text for b in blocks).lower()))
    output_words = set(re.findall(r"p\d+x\d+x\d+", " ".join(c.text for c in chunks).lower()))
    assert source_words <= output_words
    # 3. ordinals are contiguous and ids unique
    assert [c.metadata.ordinal for c in chunks] == list(range(len(chunks)))
    assert len({c.id for c in chunks}) == len(chunks)
