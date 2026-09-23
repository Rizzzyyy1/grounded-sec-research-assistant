"""Context assembly: retrieved chunks -> labelled, token-budgeted sources.

Steps: drop duplicates -> trim to the token budget by *relevance* (best first) -> order what
remains by filing and position so the model reads coherent passages -> label S1..Sn.

Source text is HTML-escaped when rendered, so retrieved filing text can never close the
``<source>`` tag and smuggle in prompt structure (see guardrails).

Two kinds of evidence are citable, both assigned labels from the same ``S1, S2, ...`` sequence so
``generation/citations.py`` can validate and cite either one the same way: :class:`Source` wraps a
retrieved passage (built here, from :func:`build_context`); :class:`FactSource` wraps one reported
XBRL value (built by ``agent/tools.py``, which is the only layer that talks to the fact store - see
its module docstring for why a citation is never synthesised for a *calculated* value that spans
more than one filing).
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass

from finsight.core.schemas import Chunk, RetrievedChunk
from finsight.processing.chunking import count_tokens


@dataclass(frozen=True)
class Source:
    id: str  # "S1", "S2", ...
    chunk: Chunk

    @property
    def label(self) -> str:
        m = self.chunk.metadata
        return f"{m.ticker} {m.form.value} FY{m.fiscal_year}, Item {m.item}"


@dataclass(frozen=True)
class FactSource:
    """A citable, reported XBRL fact - traced to the exact filing and tag it came from.

    Unlike a passage, a fact has no single sentence to quote; ``detail`` is the human-readable
    string a reader checks it against instead (value, XBRL tag, filing and accession).
    """

    id: str  # "S1", "S2", ... - the same sequence as Source, so labels never collide
    ticker: str
    fiscal_year: int
    metric: str  # canonical name, e.g. "revenue"
    tag: str  # raw XBRL concept, e.g. "RevenueFromContractWithCustomerExcludingAssessedTax"
    url: str  # the one filing this value was reported in
    detail: str

    @property
    def label(self) -> str:
        return f"{self.ticker} {self.metric} FY{self.fiscal_year}"


@dataclass(frozen=True)
class Context:
    sources: tuple[Source | FactSource, ...]
    text: str
    tokens: int

    def get(self, source_id: str) -> Source | FactSource | None:
        return next((s for s in self.sources if s.id == source_id), None)


def render_source(source: Source) -> str:
    m = source.chunk.metadata
    attrs = (
        f'id="{source.id}" company="{html.escape(m.company)}" ticker="{m.ticker}" '
        f'filing="{m.form.value} FY{m.fiscal_year}" item="{m.item}" type="{m.chunk_type.value}"'
    )
    return f"<source {attrs}>\n{html.escape(source.chunk.text, quote=False)}\n</source>"


def build_context(retrieved: Sequence[RetrievedChunk], *, budget_tokens: int) -> Context:
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    kept: list[Chunk] = []
    used = 0
    for r in retrieved:  # best first
        chunk = r.chunk
        key = " ".join(chunk.text.split())
        if chunk.id in seen_ids or key in seen_text:
            continue
        cost = count_tokens(chunk.text) + 40  # + framing overhead of the <source> wrapper
        if kept and used + cost > budget_tokens:
            continue  # a smaller, lower-ranked chunk may still fit
        seen_ids.add(chunk.id)
        seen_text.add(key)
        kept.append(chunk)
        used += cost

    kept.sort(
        key=lambda c: (c.metadata.ticker, -c.metadata.fiscal_year, c.metadata.accession,
                       c.metadata.ordinal)
    )  # fmt: skip
    sources = tuple(Source(f"S{i}", c) for i, c in enumerate(kept, start=1))
    return Context(sources, "\n\n".join(render_source(s) for s in sources), used)
