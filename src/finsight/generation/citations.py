"""Citation parsing and validation.

The model cites with ``[S1]`` labels. This module checks that every label refers to a source we
actually provided - a retrieved passage (:class:`~finsight.generation.context.Source`) or a
reported XBRL fact (:class:`~finsight.generation.context.FactSource`) - turns valid ones into
:class:`Citation` objects (with a display quote/detail and a real filing URL), and finds *uncited*
sentences - the claims a reader cannot trace to either kind of evidence.

``validate_citations`` alone is not enough to make an answer trustworthy, in two directions:

* it reports which labels are invalid, but the raw model text still contains them verbatim, so a
  reader sees what looks like a resolved citation pointing at nothing (observed live with a local
  3B model: it wrote ``[S1]`` after a figure even though no ``search_filings`` call had ever
  registered an ``S1``). ``repair_citations`` closes that gap: every bracket a *user* sees in the
  final answer must resolve to evidence this run actually produced, or it is rewritten to say so.
* a claim can go **uncited even though the run gathered real support for it** - observed live: a
  qualitative answer paraphrased a retrieved 10-K passage closely enough to share most of its
  distinctive vocabulary, but the model never wrote the bracket at all (ERROR_ANALYSIS.md 3f).
  ``attribute_claims`` closes that gap the other way: it checks every uncited sentence against
  every retrieved-but-uncited passage this run produced and, only where the sentence demonstrably
  reuses that passage's own content (not merely "a passage exists for this question"), adds the
  real citation. A sentence that matches nothing stays flagged uncited - this never manufactures
  support a tool did not actually gather (calibration and the boundary between a genuine paraphrase
  and an ungrounded, if factually correct, answer are in ERROR_ANALYSIS.md 3f).

The same lexical-support check also runs the other way, as a *diagnostic*: a citation a model
wrote can **resolve** to a real passage without that passage actually **supporting** the sentence
it is attached to. ``CitationReport.unsupported_ids`` reports this separately from
``invalid_ids`` - "resolves" and "supports the claim" are different questions, and conflating them
would hide whichever one a reader most needs to know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finsight.core.schemas import Citation
from finsight.generation.context import Context, FactSource, Source
from finsight.generation.prompts import ABSTAIN_TOKEN
from finsight.indexing.sparse_index import tokenize

_LABEL_GROUP = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]", re.IGNORECASE)
_LABEL = re.compile(r"S\d+", re.IGNORECASE)
# Split after ., ! or ? - but never before a citation label, so "... 2023. [S2]" stays one sentence.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z$\d]|\[(?!S\d))")
_QUOTE_CHARS = 280
_MIN_WORDS_FOR_CLAIM = 6
UNVERIFIED_MARKER = "[unverified]"
# Calibrated against real traces (ERROR_ANALYSIS.md 3f): a genuine paraphrase of a 10-K passage
# shared 53-100% of its distinctive terms with that passage (8-16 shared terms); an answer drawn
# from the model's own training-data familiarity, not the retrieved text, shared none with any
# retrieved passage. This is a deterministic, explainable proxy for support - not a semantic
# entailment judgement, and no LLM judge is used here (matching this project's zero-cost
# evaluation philosophy) - so it is calibrated to be conservative: a real but loosely-worded
# paraphrase may still miss it and stay flagged uncited, which is the safe direction to fail in.
_MIN_SHARED_TERMS = 4
_MIN_OVERLAP_RATIO = 0.4


@dataclass(frozen=True)
class CitationReport:
    citations: tuple[Citation, ...]
    invalid_ids: tuple[str, ...]  # labels the model used that match no provided source
    uncited_sentences: tuple[str, ...]
    #: Labels that resolve to a real passage (kind="fact" citations are out of scope here - see
    #: module docstring) whose content does not clearly support the sentence citing it. Diagnostic
    #: only, deliberately not part of `ok` - "resolves" and "supports the claim" are reported
    #: separately, not conflated into one pass/fail bit.
    unsupported_ids: tuple[str, ...] = ()

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


def support_ratio(claim: str, passage_text: str) -> float:
    """Fraction of the claim's distinctive terms also present in the passage - 0 if the claim has
    no terms to check. Exported (not underscore-prefixed) so evaluation code and tests can report
    the raw score, not just the pass/fail threshold - see :func:`_passage_supports`."""
    claim_terms = set(tokenize(strip_labels(claim)))
    if not claim_terms:
        return 0.0
    shared = claim_terms & set(tokenize(passage_text))
    return len(shared) / len(claim_terms)


def _passage_supports(claim: str, source: Source) -> bool:
    claim_terms = set(tokenize(strip_labels(claim)))
    if len(claim_terms) < _MIN_SHARED_TERMS:
        return False
    shared = claim_terms & set(tokenize(source.chunk.text))
    return len(shared) >= _MIN_SHARED_TERMS and len(shared) / len(claim_terms) >= _MIN_OVERLAP_RATIO


def _claim_sentences(answer: str) -> list[str]:
    """Sentences worth checking for a citation - skips headings, lead-ins and short connectives,
    and everything once the model has abstained (an abstain reason makes no claim to support)."""
    if answer.strip().startswith(ABSTAIN_TOKEN):
        return []
    out = []
    for raw in _SENTENCE.split(answer.strip()):
        sentence = raw.strip()
        words = len(strip_labels(sentence).split())
        if words < _MIN_WORDS_FOR_CLAIM or sentence.endswith(":"):
            continue
        out.append(sentence)
    return out


def validate_citations(answer: str, context: Context) -> CitationReport:
    citations: list[Citation] = []
    invalid: list[str] = []
    by_id: dict[str, Source | FactSource] = {}
    for label in cited_ids(answer):
        source = context.get(label)
        if source is None:
            invalid.append(label)
            continue
        citations.append(_to_citation(label, source))
        by_id[label] = source

    uncited: list[str] = []
    unsupported: set[str] = set()
    for sentence in _claim_sentences(answer):
        labels = [lbl.upper() for g in _LABEL_GROUP.findall(sentence) for lbl in _LABEL.findall(g)]
        resolved = [by_id[lbl] for lbl in labels if lbl in by_id]
        if not resolved:
            uncited.append(sentence)
            continue
        passages = [s for s in resolved if isinstance(s, Source)]
        # Only judge "supports the claim" when every resolved source on this sentence is a
        # passage - a fact citation's support is a different question (module docstring) and is
        # not checked here, so a sentence mixing kinds is left out of this diagnostic entirely.
        supported = any(_passage_supports(sentence, s) for s in passages)
        if passages and len(passages) == len(resolved) and not supported:
            unsupported.update(
                lbl for lbl in labels if lbl in by_id and isinstance(by_id[lbl], Source)
            )
    return CitationReport(
        tuple(citations), tuple(invalid), tuple(uncited), tuple(sorted(unsupported))
    )


def attribute_claims(answer: str, context: Context) -> tuple[str, CitationReport]:
    """The general fix for a claim the model stated but never cited: verify - against the
    *specific* evidence this run actually produced, not just "something was retrieved for this
    question" - whether an uncited sentence is genuinely supported by a passage nothing cited yet,
    and make that support visible as a real citation rather than leaving it to the model's memory
    to bracket correctly. A sentence that matches no passage's own content stays flagged uncited:
    this never manufactures support a tool did not actually gather. See the module docstring and
    ERROR_ANALYSIS.md 3f for calibration and the (deliberate) scope limit to passage citations.
    """
    report = validate_citations(answer, context)
    if not report.uncited_sentences:
        return answer, report

    cited_ids_ = {c.source_id for c in report.citations}
    candidates = [s for s in context.sources if s.id not in cited_ids_ and isinstance(s, Source)]
    if not candidates:
        return answer, report

    text = answer
    new_citations: list[Citation] = []
    attributed: set[str] = set()
    for sentence in report.uncited_sentences:
        best: tuple[float, Source] | None = None
        for source in candidates:
            if not _passage_supports(sentence, source):
                continue
            ratio = support_ratio(sentence, source.chunk.text)
            if best is None or ratio > best[0]:
                best = (ratio, source)
        if best is None or sentence not in text:
            continue
        source = best[1]
        text = text.replace(sentence, f"{sentence} [{source.id}]", 1)
        new_citations.append(_to_citation(source.id, source))
        attributed.add(sentence)

    if not new_citations:
        return answer, report

    remaining_uncited = tuple(s for s in report.uncited_sentences if s not in attributed)
    updated = CitationReport(
        citations=report.citations + tuple(new_citations),
        invalid_ids=report.invalid_ids,
        uncited_sentences=remaining_uncited,
        unsupported_ids=report.unsupported_ids,
    )
    return text, updated


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
