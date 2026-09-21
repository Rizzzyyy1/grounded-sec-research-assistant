"""HTML / iXBRL parsing on realistic SEC markup."""

from __future__ import annotations

import pytest

from finsight.core.exceptions import ParsingError
from finsight.processing.html_parser import normalize, parse_filing_html

pytestmark = pytest.mark.unit


def parse(body: str) -> list:  # type: ignore[type-arg]
    return parse_filing_html(f"<html><body>{body}</body></html>".encode())


def texts(blocks: list) -> list[str]:  # type: ignore[type-arg]
    return [b.text for b in blocks if not b.is_table]


def test_divs_with_only_inline_content_become_paragraphs() -> None:
    blocks = parse("<div><span>First para.</span></div><div><span>Second</span> <b>para</b>.</div>")
    assert texts(blocks) == ["First para.", "Second para."]


def test_hidden_ix_header_is_dropped() -> None:
    body = (
        "<ix:header><ix:hidden><ix:nonnumeric>SECRET-CONTEXT</ix:nonnumeric></ix:hidden>"
        "<xbrli:context>junk</xbrli:context></ix:header><div>Visible text.</div>"
    )
    assert texts(parse(body)) == ["Visible text."]


def test_ix_wrappers_are_transparent_inline_and_around_blocks() -> None:
    body = (
        "<div>Revenue was <ix:nonfraction>391</ix:nonfraction> billion.</div>"
        "<ix:nonnumeric><div>Block one.</div><div>Block two.</div></ix:nonnumeric>"
    )
    assert texts(parse(body)) == ["Revenue was 391 billion.", "Block one.", "Block two."]


def test_scripts_styles_and_comments_are_ignored() -> None:
    body = "<script>alert(1)</script><style>p{}</style><!-- note --><div>Keep me</div>"
    assert texts(parse(body)) == ["Keep me"]


def test_br_separates_words_and_nbsp_is_normalised() -> None:
    assert texts(parse("<div>a<br>b&nbsp;&nbsp;c</div>")) == ["a b c"]


def test_mixed_inline_text_and_nested_blocks_keep_order() -> None:
    blocks = parse("<div>intro <div>nested</div> outro</div>")
    assert texts(blocks) == ["intro", "nested", "outro"]


@pytest.mark.parametrize(
    "noise",
    ["12", "Page 7", "Table of Contents", "Apple Inc. | 2024 Form 10-K | 25"],
)
def test_page_furniture_is_filtered(noise: str) -> None:
    assert texts(parse(f"<div>{noise}</div><div>Real sentence here.</div>")) == [
        "Real sentence here."
    ]


def test_financial_table_merges_currency_and_percent_fragments() -> None:
    table = (
        "<table><tr><td>Net sales</td><td>$</td><td>391,035</td><td>$</td><td>383,285</td></tr>"
        "<tr><td>Change</td><td>(2</td><td>)</td><td>%</td></tr>"
        "<tr><td>Margin</td><td>46.2</td><td>%</td></tr></table>"
    )
    (block,) = parse(table)
    assert block.is_table
    assert block.rows[0] == ("Net sales", "$391,035", "$383,285")
    assert block.rows[1] == ("Change", "(2)%")
    assert block.rows[2] == ("Margin", "46.2%")
    assert block.text.splitlines()[0] == "| Net sales | $391,035 | $383,285 |"


def test_empty_cells_and_rows_are_dropped() -> None:
    (block,) = parse(
        "<table><tr><td></td><td>a</td><td> </td><td>b</td></tr><tr><td></td></tr></table>"
    )
    assert block.rows == (("a", "b"),)


def test_single_column_layout_table_becomes_paragraphs() -> None:
    blocks = parse(
        "<table><tr><td>Paragraph one.</td></tr><tr><td>Paragraph two.</td></tr></table>"
    )
    assert [b.kind for b in blocks] == ["text", "text"]
    assert texts(blocks) == ["Paragraph one.", "Paragraph two."]


def test_table_between_paragraphs_keeps_document_order() -> None:
    blocks = parse(
        "<div>Before.</div><table><tr><td>a</td><td>1</td></tr></table><div>After.</div>"
    )
    assert [b.kind for b in blocks] == ["text", "table", "text"]


def test_empty_or_garbage_input_raises_parsing_error() -> None:
    with pytest.raises(ParsingError):
        parse_filing_html(b"")


def test_normalize_collapses_whitespace_and_zero_width() -> None:
    assert normalize("  a\u200b \n\t b\xa0c ") == "a b c"


def test_hidden_content_inside_a_leaf_paragraph_does_not_leak() -> None:
    """Regression: text_content() on a paragraph included nested ix:header / script text."""
    body = "<div>Visible <ix:header>SECRET</ix:header>text<script>alert(1)</script> here.</div>"
    assert texts(parse(body)) == ["Visible text here."]
