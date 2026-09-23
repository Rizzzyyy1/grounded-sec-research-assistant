"""Citation parsing and validation.

The model cites with ``[S1]`` labels. This module checks that every label refers to a source we
actually provided - a retrieved passage (:class:`~finsight.generation.context.Source`) or a
reported XBRL fact (:class:`~finsight.generation.context.FactSource`) - turns valid ones into
:class:`Citation` objects (with a display quote/detail and a real filing URL), and finds *uncited*
sentences - the claims a reader cannot trace to either kind of evidence.

``validate_citations`` alone is not enough to make an answer trustworthy: it reports which labels
are invalid, but the raw model text still contains them verbatim, so a reader sees what looks like
a resolved citation pointing at nothing (observed live with a local 3B model: it wrote ``[S1]``
after a figure even though no ``search_filings`` call had ever registered an ``S1``).
``repair_citations`` is the general rule that closes that gap: every bracket a *user* sees in the
final answer must resolve to evidence this run actually produced, or it is rewritten to say so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finsight.core.schemas import Citation
from finsight.generation.context import Context, FactSource, Source
from finsight.generation.prompts import ABSTAIN_TOKEN

_LABEL_GROUP = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]", re.IGNORECASE)
_LABEL = re.compile(r"S\d+", re.IGNORECASE)
# Split after ., ! or ? - but never before a citation label, so "... 2023. [S2]" stays one sentence.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z$\d]|\[(?!S\d))")
_QUOTE_CHARS = 280
_MIN_WORDS_FOR_CLAIM = 6
UNVERIFIED_MARKER = "[unverified]"


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


def _to_citation(label: str, source: Source | FactSource) -> Citation:
    if isinstance(source, FactSource):
        return Citation(
            source_id=label, kind="fact", ticker=source.ticker, fiscal_year=source.fiscal_year,
            url=source.url, quote=source.detail, metric=source.metric, xbrl_tag=source.tag,
        )  # fmt: skip
    m = source.chunk.metadata
    return Citation(
        source_id=label, kind="passage", chunk_id=source.chunk.id, ticker=m.ticker, form=m.form,
        fiscal_year=m.fiscal_year, item=m.item, url=m.source_url, quote=_trim(source.chunk.text),
    )  # fmt: skip


def validate_citations(answer: str, context: Context) -> CitationReport:
    citations: list[Citation] = []
    invalid: list[str] = []
    for label in cited_ids(answer):
        source = context.get(label)
        if source is None:
            invalid.append(label)
            continue
        citations.append(_to_citation(label, source))

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


def repair_citations(answer: str, report: CitationReport) -> str:
    """Rewrite every bracket in ``answer`` so it only ever shows a citation the run can back up.

    This is the general rule, applied identically regardless of which system produced the answer
    or what the question was about: a label is kept (labels within one bracket deduplicated) only
    if it names a source in ``report.citations``; a bracket left with no valid label - an unknown
    id, a repeated invalid id, or a citation attempted with no evidence available at all - becomes
    :data:`UNVERIFIED_MARKER` instead of vanishing or staying as a dangling, unresolvable pointer.
    The claim itself is never deleted, only its citation is corrected - that keeps the answer
    useful while making an unsupported claim visibly different from a supported one.
    """
    valid_ids = {c.source_id for c in report.citations}

    def repl(match: re.Match[str]) -> str:
        kept: list[str] = []
        for raw in _LABEL.findall(match.group(1)):
            label = raw.upper()
            if label in valid_ids and label not in kept:
                kept.append(label)
        return f"[{', '.join(kept)}]" if kept else UNVERIFIED_MARKER

    return _LABEL_GROUP.sub(repl, answer)
