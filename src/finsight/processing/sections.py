"""10-K section splitter: ordered blocks -> canonical Items.

Item identity ("1A", "7", "8") becomes chunk metadata, which powers filtering and per-section
evaluation, so the splitter has to be right on messy real filings:

* A heading is a *short* block that **starts with** ``Item <n>``. Long paragraphs that merely
  begin with a cross-reference ("Item 7 of this report discusses...") are not headings.
* The same Item can be matched more than once (table of contents, running headers, in-text
  references). We keep the occurrence whose section is **longest** - the real body dwarfs a TOC
  entry - and treat the other matches as ordinary text.
* Canonical titles come from the SEC form, not from the scraped heading (which varies in case
  and punctuation).
* When too few Items are found (a filing that does not follow the layout) the whole document is
  returned as a single ``UNK`` section rather than silently dropping content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finsight.processing.html_parser import Block

#: Canonical 10-K items. Item 1C (cybersecurity) exists from FY2023; Item 6 is "[Reserved]"
#: from FY2021. Missing items are normal, not errors.
TEN_K_ITEMS: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": (
        "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases "
        "of Equity Securities"
    ),
    "6": "Reserved",
    "7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": (
        "Security Ownership of Certain Beneficial Owners and Management and Related "
        "Stockholder Matters"
    ),
    "13": "Certain Relationships and Related Transactions, and Director Independence",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}
#: Items whose presence we expect in a healthy parse (used for the detection-rate metric).
CORE_ITEMS = ("1", "1A", "7", "7A", "8")

UNKNOWN_ITEM = "UNK"
MIN_ITEMS_FOR_STRUCTURE = 3
_MAX_HEADING_CHARS = 250

_HEADING = re.compile(
    # \u2013 and \u2014 are the en/em dashes that real headings use as separators.
    r"^(?:part\s+[ivx]+\s*[,.\-\u2013\u2014:]?\s*)?item\s+(\d{1,2}\s?[a-c]?)\b"
    r"\s*[.:\-\u2013\u2014]?\s*(.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SectionBlocks:
    """A section with its blocks intact (the chunker needs to tell paragraphs from tables)."""

    item: str
    title: str
    blocks: tuple[Block, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


def match_heading(block: Block) -> tuple[str, str] | None:
    """(item id, scraped title) if the block is an Item heading, else None."""
    # Some filers (Walmart, Amazon) set the heading as a one-row, two-cell table:
    # "| Item 1A. | Risk Factors |". A multi-row table is a table of contents, never a heading.
    if block.is_table:
        if len(block.rows) != 1:
            return None
        text = " ".join(block.rows[0])
    else:
        text = block.text
    if len(text) > _MAX_HEADING_CHARS:
        return None
    m = _HEADING.match(text)
    if not m:
        return None
    item = m.group(1).replace(" ", "").upper()
    title = m.group(2).strip()
    # A real title starts with a capital, digit or bracket ("Risk Factors", "[Reserved]");
    # "Item 7 of this report describes..." is a cross-reference sentence, not a heading.
    if item not in TEN_K_ITEMS or (title and not (title[0].isupper() or title[0] in "[(\"'")):
        return None
    return item, title


def _weight(blocks: list[Block]) -> int:
    return sum(len(b.text) for b in blocks)


def split_sections(blocks: list[Block]) -> list[SectionBlocks]:
    candidates = [(i, *h) for i, b in enumerate(blocks) if (h := match_heading(b))]
    if not candidates:
        return [SectionBlocks(UNKNOWN_ITEM, "Full document", tuple(blocks))] if blocks else []

    # Span of each candidate = blocks until the next candidate of *any* item.
    ends = [c[0] for c in candidates[1:]] + [len(blocks)]
    spans = [(cand, blocks[cand[0] + 1 : end]) for cand, end in zip(candidates, ends, strict=True)]

    # For each item keep the candidate with the heaviest span (real body beats TOC entry).
    best: dict[str, tuple[int, int]] = {}  # item -> (block index, weight)
    for (idx, item, _title), span in spans:
        w = _weight(span)
        if item not in best or w > best[item][1]:
            best[item] = (idx, w)

    chosen = sorted((idx, item) for item, (idx, _w) in best.items())
    if len(chosen) < MIN_ITEMS_FOR_STRUCTURE:
        return [SectionBlocks(UNKNOWN_ITEM, "Full document", tuple(blocks))]

    sections: list[SectionBlocks] = []
    boundaries = [idx for idx, _ in chosen] + [len(blocks)]
    for (idx, item), end in zip(chosen, boundaries[1:], strict=True):
        body = tuple(blocks[idx + 1 : end])
        if body:
            sections.append(SectionBlocks(item, TEN_K_ITEMS[item], body))
    return sections


def core_items_found(sections: list[SectionBlocks]) -> set[str]:
    return {s.item for s in sections} & set(CORE_ITEMS)
