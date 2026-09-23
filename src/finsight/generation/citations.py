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
# Calibrated against real traces from a manual audit reading the *full* underlying passage text a
# citation resolves to, not the trimmed display quote - the two can differ a lot on a long chunk,
# and an earlier pass of this calibration was itself wrong for exactly that reason (ERROR_ANALYSIS
# 3g). The audit found no threshold on this bag-of-words ratio cleanly separates two real classes
# of answer: a genuine, single-topic paraphrase of a passage (observed 0.87-0.94) and a compound
# sentence where only *one clause* is genuinely sourced but the sentence as a whole still shares
# real vocabulary with an off-topic passage - confirmed live: a claim about "growing its
# e-commerce business" that also happened to mention "ongoing growth ... for associates" was
# attached to a passage that was entirely about employee benefits ("growth" as career
# development, never e-commerce), because that one clause alone scored 0.61. A plausible, if
# generic and boilerplate, single-clause match scored a close 0.64. Given no ratio reliably tells
# these apart, the threshold is set above both (0.75) rather than between them - deliberately
# trading away some real coverage (a real match that happens to score in the 0.6-0.7 range is
# left uncited) for not attaching the confirmed-false one. This is a deterministic, explainable
# proxy for support, not a semantic entailment judgement - no LLM judge is used here, matching
# this project's zero-cost evaluation philosophy - and it stays calibrated conservative on
# purpose: a real but loosely-worded or partly-unsourced claim may miss it and stay flagged
# uncited, which is the safe direction to fail in, not the reverse. Compound, multi-topic
# sentences remain a known, disclosed residual risk this ratio-based check cannot fully close -
# see ERROR_ANALYSIS.md 3g's recommendation.
_MIN_SHARED_TERMS = 4
_MIN_OVERLAP_RATIO = 0.75
# A shared contiguous n-gram - the claim reusing an actual run of the passage's own wording, not
# just its vocabulary scattered anywhere in it - is a stronger signal than bag-of-words overlap
# alone and catches cases the ratio check admits on vocabulary alone (ERROR_ANALYSIS.md 3g).
_NGRAM_SIZE = 4
_MIN_SHARED_NGRAMS = 2
# Neither bag-of-words nor n-gram overlap catches a claim that states the *opposite* of what the
# passage says while reusing almost all of its wording ("revenue increased" vs "revenue
# decreased") - confirmed by construction: such a pair still shares 6 four-grams. A word from the
# claim whose paired opposite appears in the passage vetoes the match outright, regardless of how
# high the overlap score is.
_DIRECTION_PAIRS = (
    ("increase", "decrease"), ("increased", "decreased"), ("increasing", "decreasing"),
    ("increases", "decreases"), ("higher", "lower"), ("rose", "fell"), ("rising", "falling"),
    ("grew", "declined"), ("growth", "decline"), ("grow", "shrink"), ("gain", "loss"),
    ("gained", "lost"), ("gains", "losses"), ("up", "down"), ("above", "below"),
    ("expanded", "contracted"), ("improved", "worsened"), ("stronger", "weaker"),
    ("outperformed", "underperformed"), ("beat", "missed"), ("exceeded", "fell short of"),
    ("more", "less"), ("profit", "loss"), ("profitable", "unprofitable"), ("record", "worst"),
)  # fmt: skip
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


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


def _ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _opposite_direction_present(claim: str, passage_text: str) -> bool:
    """True if the claim and passage use opposite directional/polarity words for what would
    otherwise read as the same statement - see :data:`_DIRECTION_PAIRS`."""
    claim_words = set(tokenize(claim))
    passage_words = set(tokenize(passage_text))
    return any(
        (a in claim_words and b in passage_words) or (b in claim_words and a in passage_words)
        for a, b in _DIRECTION_PAIRS
    )


def _year_mismatch(claim: str, source: Source) -> bool:
    """True if the claim names a specific fiscal year that is not the source's own fiscal year -
    a passage from FY2022 does not establish a claim about FY2024 just because the rest of the
    sentence reads the same way. Silent (no mismatch) when the claim names no year at all."""
    claim_years = {int(y) for y in _YEAR.findall(claim)}
    return bool(claim_years) and source.chunk.metadata.fiscal_year not in claim_years


def _passage_supports(claim: str, source: Source) -> bool:
    stripped = strip_labels(claim)
    claim_terms = set(tokenize(stripped))
    if len(claim_terms) < _MIN_SHARED_TERMS:
        return False
    passage_text = source.chunk.text
    shared = claim_terms & set(tokenize(passage_text))
    if len(shared) < _MIN_SHARED_TERMS or len(shared) / len(claim_terms) < _MIN_OVERLAP_RATIO:
        return False
    shared_ngrams = _ngrams(tokenize(stripped), _NGRAM_SIZE) & _ngrams(
        tokenize(passage_text), _NGRAM_SIZE
    )
    if len(shared_ngrams) < _MIN_SHARED_NGRAMS:
        return False
    return not (
        _opposite_direction_present(stripped, passage_text) or _year_mismatch(stripped, source)
    )


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
