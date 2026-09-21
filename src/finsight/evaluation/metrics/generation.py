"""Generation metrics that need no LLM: correctness rules, abstention, citation hygiene."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from finsight.core.schemas import Answer
from finsight.evaluation.datasets import Expected, GoldExample
from finsight.evaluation.metrics.numeric import numeric_match


def contains_all(text: str, names: Sequence[str]) -> bool:
    lowered = text.lower()
    return all(n.lower() in lowered for n in names)


def rule_correct(expected: Expected, answer: Answer) -> bool | None:
    """Rule-based correctness, or ``None`` when the example needs a judge or retrieval scoring.

    * abstain expected  -> the system must have abstained;
    * otherwise         -> it must not have abstained, must contain the expected names, and its
                           text must state the expected figure.
    """
    if expected.abstain:
        return answer.abstained
    if expected.numeric is None and not expected.answer_contains:
        return None
    if answer.abstained:
        return False
    if expected.answer_contains and not contains_all(answer.text, expected.answer_contains):
        return False
    return expected.numeric is None or numeric_match(answer.text, expected.numeric)


@dataclass(frozen=True)
class AbstentionScores:
    tp: int  # should abstain, did
    fp: int  # should answer, abstained (over-abstention)
    fn: int  # should abstain, answered (the dangerous one)
    tn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def abstention_scores(pairs: Sequence[tuple[GoldExample, Answer]]) -> AbstentionScores:
    tp = fp = fn = tn = 0
    for example, answer in pairs:
        should, did = example.expected.abstain, answer.abstained
        tp += should and did
        fp += (not should) and did
        fn += should and (not did)
        tn += (not should) and (not did)
    return AbstentionScores(tp, fp, fn, tn)


@dataclass(frozen=True)
class CitationHygiene:
    has_citation: bool
    invalid_citations: int
    uncited_claims: int
    unverified_figures: int

    @property
    def clean(self) -> bool:
        return self.has_citation and not (
            self.invalid_citations or self.uncited_claims or self.unverified_figures
        )


def citation_hygiene(answer: Answer) -> CitationHygiene:
    w = answer.warnings
    return CitationHygiene(
        has_citation=bool(answer.citations),
        invalid_citations=sum(x.startswith("citation to unknown") for x in w),
        uncited_claims=sum(x.startswith("uncited claim") for x in w),
        unverified_figures=sum(x.startswith("unverified figure") for x in w),
    )
