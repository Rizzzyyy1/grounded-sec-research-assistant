"""Citation parsing and validation.

The model cites with ``[S1]`` labels. This module checks that every label refers to a source we
actually provided, turns valid ones into :class:`Citation` objects (with a display quote and the
filing URL), and finds *uncited* sentences - the claims a reader cannot trace to a passage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finsight.core.schemas import Citation
from finsight.generation.context import Context
from finsight.generation.prompts import ABSTAIN_TOKEN

_LABEL_GROUP = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]", re.IGNORECASE)
_LABEL = re.compile(r"S\d+", re.IGNORECASE)
# Split after ., ! or ? - but never before a citation label, so "... 2023. [S2]" stays one sentence.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z$\d]|\[(?!S\d))")
_QUOTE_CHARS = 280
_MIN_WORDS_FOR_CLAIM = 6


@dataclass(frozen=True)
class CitationReport:
    citations: tuple[Citation, ...]
    invalid_ids: tuple[str, ...]  # labels the model used that match no provided source
    uncited_sentences: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.invalid_ids and not self.uncited_sentences


def cited_ids(text: str) -> list[str]:
    """Labels in order of first use, e.g. ``"[S2][S1, S3] ... [S2]"`` -> ``[S2, S1, S3]``."""
    seen: list[str] = []
    for group in _LABEL_GROUP.findall(text):
        for found in _LABEL.findall(group):
            label = found.upper()
            if label not in seen:
                seen.append(label)
    return seen


def strip_labels(text: str) -> str:
    return _LABEL_GROUP.sub("", text)


def _trim(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _QUOTE_CHARS else flat[: _QUOTE_CHARS - 1].rstrip() + "…"


def validate_citations(answer: str, context: Context) -> CitationReport:
    citations: list[Citation] = []
    invalid: list[str] = []
    for label in cited_ids(answer):
        source = context.get(label)
        if source is None:
            invalid.append(label)
            continue
        m = source.chunk.metadata
        citations.append(
            Citation(
                source_id=label, chunk_id=source.chunk.id, ticker=m.ticker, form=m.form,
                fiscal_year=m.fiscal_year, item=m.item, url=m.source_url,
                quote=_trim(source.chunk.text),
            )
        )  # fmt: skip

    uncited: list[str] = []
    if not answer.strip().startswith(ABSTAIN_TOKEN):
        for raw in _SENTENCE.split(answer.strip()):
            sentence = raw.strip()
            words = len(strip_labels(sentence).split())
            if words < _MIN_WORDS_FOR_CLAIM or sentence.endswith(":"):
                continue  # headings, lead-ins and short connectives make no claim
            labels = _LABEL_GROUP.findall(sentence)
            if not any(context.get(lbl.upper()) for g in labels for lbl in _LABEL.findall(g)):
                uncited.append(sentence)
    return CitationReport(tuple(citations), tuple(invalid), tuple(uncited))
