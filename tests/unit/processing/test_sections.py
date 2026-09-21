"""Item detection: TOC handling, table-style headings, cross-references, fallbacks."""

from __future__ import annotations

import pytest

from finsight.processing.html_parser import Block
from finsight.processing.sections import (
    CORE_ITEMS,
    UNKNOWN_ITEM,
    core_items_found,
    match_heading,
    split_sections,
)

pytestmark = pytest.mark.unit


def t(text: str) -> Block:
    return Block("text", text)


def row_table(*cells: str) -> Block:
    return Block("table", "| " + " | ".join(cells) + " |", (tuple(cells),))


def toc_table() -> Block:
    rows = (("Item 1", "Business", "3"), ("Item 1A", "Risk Factors", "9"), ("Item 7", "MD&A", "30"))
    return Block("table", "toc", rows)


BODY = "x" * 200


def full_doc() -> list[Block]:
    return [
        t("Cover page text"),
        toc_table(),
        t("Item 1. Business"), t(BODY),
        t("Item 1A. Risk Factors"), t(BODY), t(BODY),
        t("Item 7. Management's Discussion and Analysis"), t(BODY),
        t("Item 7A. Quantitative and Qualitative Disclosures About Market Risk"), t(BODY),
        t("Item 8. Financial Statements and Supplementary Data"), t(BODY),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Item 1A. Risk Factors", ("1A", "Risk Factors")),
        ("ITEM 7. MANAGEMENT'S DISCUSSION", ("7", "MANAGEMENT'S DISCUSSION")),
        ("Item 1 A. Risk Factors", ("1A", "Risk Factors")),
        ("PART II, Item 8. Financial Statements", ("8", "Financial Statements")),
        ("Item 7 \u2013 MD&A", ("7", "MD&A")),
        ("Item 6. [Reserved]", ("6", "[Reserved]")),
        ("Item 9B.", ("9B", "")),
    ],
)
def test_headings_are_recognised(text: str, expected: tuple[str, str]) -> None:
    assert match_heading(t(text)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Item 7 of this report describes the results",  # cross-reference sentence
        "Items 1 and 2 are discussed below",
        "Item 99. Nonexistent",
        "See Item 1A. Risk Factors for more",
        "Item 1A. Risk Factors " + "y" * 300,  # too long to be a heading
        "Business",
    ],
)
def test_non_headings_are_rejected(text: str) -> None:
    assert match_heading(t(text)) is None


def test_one_row_table_is_a_heading_but_a_toc_is_not() -> None:
    assert match_heading(row_table("Item 1A.", "Risk Factors")) == ("1A", "Risk Factors")
    assert match_heading(toc_table()) is None


def test_splits_into_canonical_items_with_canonical_titles() -> None:
    sections = split_sections(full_doc())
    assert [s.item for s in sections] == ["1", "1A", "7", "7A", "8"]
    assert sections[1].title == "Risk Factors"
    assert sections[2].title.startswith("Management's Discussion")
    assert len(sections[1].blocks) == 2
    assert core_items_found(sections) == set(CORE_ITEMS)


def test_preamble_and_toc_are_not_part_of_any_section() -> None:
    joined = " ".join(s.text for s in split_sections(full_doc()))
    assert "Cover page text" not in joined
    assert "toc" not in joined


def test_duplicate_text_toc_entries_lose_to_the_real_body() -> None:
    blocks = [
        t("Item 1. Business"), t("Item 1A. Risk Factors"), t("Item 7. MD&A"),  # text-style TOC
        t("Item 1. Business"), t(BODY),
        t("Item 1A. Risk Factors"), t(BODY), t(BODY), t(BODY),
        t("Item 7. MD&A"), t(BODY),
    ]  # fmt: skip
    sections = {s.item: s for s in split_sections(blocks)}
    assert set(sections) == {"1", "1A", "7"}
    assert len(sections["1A"].blocks) == 3  # the body, not the empty TOC span


def test_table_style_headings_split_like_text_headings() -> None:
    blocks = [
        toc_table(),
        row_table("Item 1.", "Business"), t(BODY),
        row_table("Item 1A.", "Risk Factors"), t(BODY),
        row_table("Item 7.", "MD&A"), t(BODY),
    ]  # fmt: skip
    assert [s.item for s in split_sections(blocks)] == ["1", "1A", "7"]


def test_in_body_cross_references_do_not_split_a_section() -> None:
    blocks = full_doc()
    blocks.insert(
        6, t("Item 7 of this report describes results in detail and is long enough to be text")
    )
    sections = split_sections(blocks)
    assert [s.item for s in sections] == ["1", "1A", "7", "7A", "8"]
    assert any("Item 7 of this report" in b.text for b in sections[1].blocks)


def test_too_few_items_falls_back_to_a_single_unknown_section() -> None:
    blocks = [t("Item 1. Business"), t(BODY), t("prose"), t("more prose")]
    (only,) = split_sections(blocks)
    assert only.item == UNKNOWN_ITEM
    assert len(only.blocks) == len(blocks)  # nothing is dropped


def test_no_headings_at_all_is_one_unknown_section_and_empty_input_is_empty() -> None:
    assert split_sections([t("just prose")])[0].item == UNKNOWN_ITEM
    assert split_sections([]) == []


def test_empty_sections_are_omitted() -> None:
    blocks = full_doc()
    blocks.insert(4, t("Item 1B. Unresolved Staff Comments"))  # immediately followed by 1A heading
    assert "1B" not in [s.item for s in split_sections(blocks)]
