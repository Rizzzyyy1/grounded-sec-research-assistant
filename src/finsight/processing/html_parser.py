"""SEC HTML / inline-XBRL cleaner: raw filing -> ordered text and table blocks.

What real filings look like (and what this handles):

* a huge hidden ``ix:header`` full of XBRL contexts (tens of thousands of nodes in a bank's 10-K)
  that is *not* visible text -> dropped;
* ``ix:nonnumeric`` / ``ix:nonfraction`` wrappers that sometimes surround whole blocks or single
  numbers -> treated as transparent;
* "paragraphs" that are ``<div><span>text</span></div>`` with no ``<p>`` at all -> a container
  with no block-level child becomes one paragraph;
* financial tables whose ``$``, ``)`` and ``%`` sit in cells of their own -> merged back onto
  their number;
* running page headers / footers and bare page numbers -> filtered out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from lxml import etree, html

from finsight.core.exceptions import ParsingError

_DROP = {"script", "style", "head", "title", "meta", "link", "noscript", "ix:header"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_TEXT_LEAF = {"p", "li", *_HEADINGS}
_CONTAINERS = {
    "div", "body", "html", "section", "article", "ul", "ol", "blockquote", "center", "main",
    "header", "footer", "dl", "dd", "dt", "form", "pre",
}  # fmt: skip
_BLOCKISH = _CONTAINERS | _TEXT_LEAF | {"table"}
_BLOCKISH_XPATH = etree.XPath("|".join(f".//{t}" for t in sorted(_BLOCKISH - {"html", "body"})))

_WS = re.compile(r"\s+")
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")
_PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,3}$", re.IGNORECASE)
_TOC_LINK = re.compile(r"^table of contents$", re.IGNORECASE)
# e.g. "Apple Inc. | 2024 Form 10-K | 25" running footers
_RUNNING_FOOTER = re.compile(r"^.{0,60}\|\s*(?:fy)?\s*\d{4}\s*form\s*10-?[kq]\s*\|\s*\d+$", re.I)
_CURRENCY_ATTACH_LEFT = {")", "%", ")%", "pts", "bps"}


@dataclass(frozen=True)
class Block:
    """One visible unit of a filing, in document order."""

    kind: Literal["text", "table"]
    text: str  # paragraph text, or a markdown rendering of the table
    rows: tuple[tuple[str, ...], ...] = ()  # table cells (empty for text blocks)

    @property
    def is_table(self) -> bool:
        return self.kind == "table"


def normalize(text: str) -> str:
    return _WS.sub(" ", _ZERO_WIDTH.sub("", text.replace("\xa0", " "))).strip()


def _is_noise(text: str) -> bool:
    return bool(
        not text or _PAGE_NUMBER.match(text) or _TOC_LINK.match(text) or _RUNNING_FOOTER.match(text)
    )


# --------------------------------------------------------------------------- tables
def _clean_row(cells: list[str]) -> list[str]:
    """Re-attach '$' / ')' / '%' fragments that SEC tables put in cells of their own."""
    out: list[str] = []
    prefix = ""
    for cell in cells:
        if not cell:
            continue
        if cell in {"$", "(", "$(", "($"}:
            prefix += cell
            continue
        if cell in _CURRENCY_ATTACH_LEFT and out:
            out[-1] += cell
            continue
        out.append(prefix + cell)
        prefix = ""
    return out


def table_rows(table: html.HtmlElement) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    for tr in table.iter("tr"):
        cells = [normalize(td.text_content()) for td in tr if td.tag in {"td", "th"}]
        cleaned = _clean_row(cells)
        if cleaned:
            rows.append(tuple(cleaned))
    return rows


def rows_to_markdown(rows: list[tuple[str, ...]]) -> str:
    return "\n".join(f"| {' | '.join(row)} |" if len(row) > 1 else row[0] for row in rows)


def _table_blocks(table: html.HtmlElement) -> list[Block]:
    rows = table_rows(table)
    if not rows:
        return []
    # A "table" that is really layout for prose (single column) is emitted as paragraphs.
    if all(len(r) == 1 for r in rows):
        return [Block("text", r[0]) for r in rows if not _is_noise(r[0])]
    return [Block("table", rows_to_markdown(rows), tuple(rows))]


# --------------------------------------------------------------------------- walking
def _blocky(el: html.HtmlElement) -> bool:
    """Does this (inline-looking) element contain block-level content?"""
    return bool(_BLOCKISH_XPATH(el))


def _emit_text(text: str, out: list[Block]) -> None:
    text = normalize(text)
    if not _is_noise(text):
        out.append(Block("text", text))


def _walk(el: html.HtmlElement, out: list[Block]) -> None:
    tag = el.tag
    if not isinstance(tag, str) or tag in _DROP:
        return
    if tag == "table":
        out.extend(_table_blocks(el))
        return
    if tag in _TEXT_LEAF and not _blocky(el):
        _emit_text(el.text_content(), out)
        return
    if tag in _TEXT_LEAF or tag in _CONTAINERS or _blocky(el):
        _walk_container(el, out)
        return
    _emit_text(el.text_content(), out)  # inline element met at block level


def _walk_container(el: html.HtmlElement, out: list[Block]) -> None:
    """Split a container into paragraphs (runs of inline content) and nested blocks."""
    if not _blocky(el):
        _emit_text(el.text_content(), out)
        return
    buffer: list[str] = [el.text or ""]

    def flush() -> None:
        _emit_text("".join(buffer), out)
        buffer.clear()

    for child in el:
        tag = child.tag
        if not isinstance(tag, str):  # comment / processing instruction
            buffer.append(child.tail or "")
            continue
        if tag in _DROP:
            buffer.append(child.tail or "")
            continue
        if tag in _BLOCKISH or _blocky(child):
            flush()
            _walk(child, out)
        elif tag == "br":
            buffer.append(" ")
        else:
            buffer.append(child.text_content())
        buffer.append(child.tail or "")
    flush()


def parse_filing_html(raw: bytes) -> list[Block]:
    """Parse a filing's primary document into ordered blocks (see module docstring)."""
    try:
        root = html.fromstring(raw)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as exc:
        raise ParsingError(f"could not parse filing HTML: {exc}") from exc
    # Remove hidden / non-visible elements *up front*. Handling them only while walking would
    # miss any that sit inside a leaf paragraph, where text_content() would include them.
    for element in [el for el in root.iter() if isinstance(el.tag, str) and el.tag in _DROP]:
        element.drop_tree()  # keeps the element's tail text
    for br in root.iter("br"):
        br.tail = " " + (br.tail or "")  # text_content() ignores <br>; keep words apart
    blocks: list[Block] = []
    _walk(root, blocks)
    return blocks
