"""Retrieval, numeric and generation metrics against hand-computed values."""

from __future__ import annotations

import math

import pytest

from finsight.core.schemas import Answer, QueryType
from finsight.evaluation.datasets import Expected, GoldExample, GoldSource, NumericExpectation
from finsight.evaluation.metrics.generation import (
    abstention_scores,
    citation_hygiene,
    contains_all,
    rule_correct,
)
from finsight.evaluation.metrics.numeric import extract_candidates, numeric_match, relative_error
from finsight.evaluation.metrics.retrieval import is_relevant, score_ranking

from tests.unit.conftest import ChunkFactory  # isort: skip

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ retrieval
def test_relevance_is_section_level_and_item_none_matches_the_whole_filing(
    make_chunk: ChunkFactory,
) -> None:
    c = make_chunk("x y", ticker="AAPL", year=2024, item="1A")
    assert is_relevant(c, [GoldSource(ticker="AAPL", fiscal_year=2024, item="1A")])
    assert not is_relevant(c, [GoldSource(ticker="AAPL", fiscal_year=2024, item="7")])
    assert not is_relevant(c, [GoldSource(ticker="AAPL", fiscal_year=2023, item="1A")])
    assert not is_relevant(c, [GoldSource(ticker="MSFT", fiscal_year=2024, item="1A")])
    assert is_relevant(c, [GoldSource(ticker="AAPL", fiscal_year=2024, item=None)])


def test_scores_match_hand_computation(make_chunk: ChunkFactory) -> None:
    gold = [GoldSource(ticker="AAPL", fiscal_year=2024, item="1A")]
    rel = lambda: make_chunk("r", ticker="AAPL", year=2024, item="1A")  # noqa: E731
    non = lambda: make_chunk("n", ticker="AAPL", year=2024, item="7")  # noqa: E731
    s = score_ranking([non(), rel(), non(), rel()], gold, ks=(1, 2, 4))
    assert s.mrr == pytest.approx(0.5)  # first relevant at rank 2
    assert (s.hit[1], s.hit[2], s.hit[4]) == (0.0, 1.0, 1.0)
    assert (s.recall[1], s.recall[2]) == (0.0, 1.0)
    # nDCG@2: dcg = 1/log2(3); ideal = 1/log2(2) + 1/log2(3)
    assert s.ndcg[2] == pytest.approx((1 / math.log2(3)) / (1 + 1 / math.log2(3)))


def test_recall_counts_gold_sources_found_not_chunks(make_chunk: ChunkFactory) -> None:
    gold = [GoldSource(ticker="AAPL", fiscal_year=2024, item="1A"),
            GoldSource(ticker="AAPL", fiscal_year=2023, item="1A")]  # fmt: skip
    ranked = [make_chunk("a", year=2024), make_chunk("b", year=2024), make_chunk("c", year=2024)]
    s = score_ranking(ranked, gold, ks=(3,))
    assert s.recall[3] == 0.5  # three chunks, but only one of the two sources


def test_no_relevant_result_scores_zero_and_empty_ranking_is_safe(make_chunk: ChunkFactory) -> None:
    gold = [GoldSource(ticker="ZZZ", fiscal_year=2024, item="1A")]
    s = score_ranking([make_chunk("x")], gold, ks=(1,))
    assert (s.mrr, s.hit[1], s.recall[1], s.ndcg[1]) == (0.0, 0.0, 0.0, 0.0)
    assert score_ranking([], gold, ks=(3,)).mrr == 0.0
    with pytest.raises(ValueError, match="gold source"):
        score_ranking([], [], ks=(1,))


def test_flat_naming() -> None:
    flat = score_ranking([], [GoldSource(ticker="A", fiscal_year=2024)], ks=(1, 8)).flat()
    assert set(flat) == {"mrr", "hit@1", "hit@8", "recall@1", "recall@8", "ndcg@1", "ndcg@8"}


# ------------------------------------------------------------------ numeric
USD = NumericExpectation(value=391_035e6, unit="usd", rel_tol=0.001)
RATIO = NumericExpectation(value=0.462, unit="ratio", rel_tol=0.02)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Net sales were $391,035 million.", True),
        ("Revenue was $391.0 billion in FY2024", True),
        ("about 391,035 (in millions)", True),
        ("It was 391,035", True),  # statements are in millions
        ("$391,035,000,000", True),
        ("about $390 billion", False),
        ("Sales fell to $383,285 million", False),
        ("no figures here", False),
        ("In 2024 the company grew", False),
    ],
)
def test_usd_matching(text: str, expected: bool) -> None:
    assert numeric_match(text, USD) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Gross margin was 46.2%.", True), ("46.2 percent", True), ("0.462", True),
        ("46.9%", True), ("The margin was 46%", True), ("52%", False), ("-46.2%", False),
    ],
)  # fmt: skip
def test_ratio_matching(text: str, expected: bool) -> None:
    assert numeric_match(text, RATIO) is expected


def test_negative_values_and_units() -> None:
    loss = NumericExpectation(value=-2_500e6, unit="usd", rel_tol=0.01)
    assert numeric_match("a net loss of $(2,500) million", loss) or numeric_match(
        "-$2,500 million", loss
    )
    eps = NumericExpectation(value=6.08, unit="usd_per_share", rel_tol=0.005)
    assert numeric_match("Diluted EPS was $6.08", eps)
    assert not numeric_match("Diluted EPS was $6.80", eps)


def test_years_are_not_mistaken_for_figures() -> None:
    """Regression: 'fiscal 2024' x 1e6 (the 'stated in millions' fallback) read as $2.024 billion,
    falsely matching any company whose real figure is about $2.0B."""
    two_billion = NumericExpectation(value=2.024e9, unit="usd", rel_tol=0.001)
    assert not numeric_match("results for fiscal 2024 were strong", two_billion)
    assert not numeric_match("In 2024 and 2023, the company grew", two_billion)
    assert numeric_match("assets of $2,024 million", two_billion)  # a real figure still matches
    assert numeric_match("assets of 2024 million", two_billion)  # explicit scale word: a figure
    assert 2024.0 not in extract_candidates("FY2024", "usd_per_share")


def test_candidates_and_relative_error() -> None:
    assert 0.462 in extract_candidates("46.2%", "ratio")
    assert 391_035e6 in extract_candidates("$391,035 million", "usd")
    assert relative_error(110, 100) == pytest.approx(0.1)
    assert relative_error(0.2, 0) == 0.2


# ------------------------------------------------------------------ generation
def ex(expected: Expected, qtype: QueryType = QueryType.NUMERIC) -> GoldExample:
    return GoldExample(id="x", split="dev", type=qtype, question="What was it exactly?",
                       expected=expected, provenance="xbrl")  # fmt: skip


def ans(text: str = "", *, abstained: bool = False, **kw: object) -> Answer:
    return Answer(question="q", text=text, abstained=abstained, **kw)  # type: ignore[arg-type]


def test_rule_correct_numeric_names_and_abstention() -> None:
    num = Expected(numeric=USD)
    assert rule_correct(num, ans("$391,035 million")) is True
    assert rule_correct(num, ans("$1 million")) is False
    assert rule_correct(num, ans("declined", abstained=True)) is False
    cmp_ = Expected(numeric=RATIO, answer_contains=("Apple",))
    assert rule_correct(cmp_, ans("Apple had 46.2%")) is True
    assert rule_correct(cmp_, ans("Microsoft had 46.2%")) is False  # right number, wrong company
    assert rule_correct(Expected(abstain=True), ans("declined", abstained=True)) is True
    assert rule_correct(Expected(abstain=True), ans("Sure, buy it")) is False
    assert rule_correct(Expected(), ans("anything")) is None  # needs retrieval scoring / a judge


def test_expected_cannot_mix_abstention_with_content() -> None:
    with pytest.raises(ValueError, match="abstain"):
        Expected(abstain=True, numeric=USD)


def test_contains_all_is_case_insensitive() -> None:
    assert contains_all("APPLE and microsoft", ("apple", "Microsoft"))
    assert not contains_all("apple", ("apple", "microsoft"))


def test_abstention_confusion_matrix() -> None:
    pairs = [
        (ex(Expected(abstain=True)), ans(abstained=True)),  # tp
        (ex(Expected(abstain=True)), ans("advice")),  # fn (dangerous)
        (ex(Expected(numeric=USD)), ans(abstained=True)),  # fp (over-abstention)
        (ex(Expected(numeric=USD)), ans("$391,035 million")),  # tn
    ]
    s = abstention_scores(pairs)
    assert (s.tp, s.fp, s.fn, s.tn) == (1, 1, 1, 1)
    assert (s.precision, s.recall, s.f1) == (0.5, 0.5, 0.5)
    empty = abstention_scores([])
    assert (empty.precision, empty.recall) == (1.0, 1.0)


def test_citation_hygiene_from_warnings() -> None:
    clean = citation_hygiene(ans("x", citations=()))
    assert not clean.has_citation and not clean.clean
    from finsight.core.schemas import Citation, FormType  # noqa: PLC0415

    cite = Citation(source_id="S1", chunk_id="c", ticker="AAPL", form=FormType.TEN_K,
                    fiscal_year=2024, item="7", url="u", quote="q")  # fmt: skip
    good = citation_hygiene(ans("x", citations=(cite,)))
    assert good.clean
    bad = citation_hygiene(ans("x", citations=(cite,),
                               warnings=("citation to unknown source S9", "uncited claim: a", "unverified figure: $5")))  # fmt: skip
    assert (bad.invalid_citations, bad.uncited_claims, bad.unverified_figures) == (1, 1, 1)
    assert not bad.clean


def test_citation_hygiene_reports_which_kinds_of_source_were_cited() -> None:
    """citation_kinds is diagnostic only - it must never change `clean`, so old runs (from before
    fact citations existed) stay comparable to new ones on the pass/fail rate."""
    from finsight.core.schemas import Citation, FormType  # noqa: PLC0415

    passage = Citation(source_id="S1", kind="passage", chunk_id="c", ticker="AAPL",
                       form=FormType.TEN_K, fiscal_year=2024, item="7", url="u", quote="q")  # fmt: skip
    fact = Citation(source_id="S2", kind="fact", ticker="AAPL", fiscal_year=2024, url="u",
                    quote="q", metric="revenue", xbrl_tag="Revenues")  # fmt: skip

    only_fact = citation_hygiene(ans("x", citations=(fact,)))
    assert only_fact.citation_kinds == frozenset({"fact"}) and only_fact.clean

    both = citation_hygiene(ans("x", citations=(passage, fact)))
    assert both.citation_kinds == frozenset({"passage", "fact"}) and both.clean

    none = citation_hygiene(ans("x", citations=()))
    assert none.citation_kinds == frozenset()
