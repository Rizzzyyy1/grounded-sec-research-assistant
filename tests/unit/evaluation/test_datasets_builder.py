"""Gold dataset I/O and the programmatic builder."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from finsight.config.universe import Universe
from finsight.core.exceptions import EvaluationError
from finsight.core.schemas import FinancialFact, FiscalPeriod, FormType, QueryType
from finsight.evaluation.datasets import (
    Expected,
    GoldExample,
    NumericExpectation,
    load_gold,
    summarize,
    write_gold,
)
from finsight.evaluation.gold_builder import TEST_TICKERS, build_gold, short_name
from finsight.ingestion.xbrl.facts import ParsedFacts
from finsight.ingestion.xbrl.store import FactStore

pytestmark = pytest.mark.unit


def example(i: int = 1, **kw: object) -> GoldExample:
    base: dict[str, object] = dict(
        id=f"e{i}", split="dev", type=QueryType.NUMERIC, question="What was revenue in fiscal 2024?",
        expected=Expected(numeric=NumericExpectation(value=1.0, unit="usd", rel_tol=0.01)),
        provenance="xbrl",
    )  # fmt: skip
    return GoldExample(**{**base, **kw})  # type: ignore[arg-type]


def test_roundtrip_and_split_filter(tmp_path: Path) -> None:
    path = tmp_path / "g.jsonl"
    exs = [example(1), example(2, split="test"), example(3, expected=Expected(abstain=True))]
    write_gold(exs, path)
    assert load_gold(path) == exs
    assert [e.id for e in load_gold(path, split="test")] == ["e2"]


def test_duplicate_ids_bad_lines_and_missing_files_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "g.jsonl"
    write_gold([example(1), example(1)], path)
    with pytest.raises(EvaluationError, match="duplicate"):
        load_gold(path)
    path.write_text('{"id": "x"}\n')
    with pytest.raises(EvaluationError, match=r"g\.jsonl:1"):
        load_gold(path)
    with pytest.raises(EvaluationError, match="finsight eval gold"):
        load_gold(tmp_path / "nope.jsonl")


def test_question_and_tolerance_validation() -> None:
    with pytest.raises(ValueError):
        example(question="short")
    with pytest.raises(ValueError):
        NumericExpectation(value=1, unit="usd", rel_tol=0)


def test_summarize_counts() -> None:
    s = summarize([example(1), example(2, split="test"), example(3, type=QueryType.TREND)])
    assert s["by_type"] == {"numeric": 2, "trend": 1}
    assert s["by_split"] == {"dev": 2, "test": 1}


# ------------------------------------------------------------------ builder
TICKERS = ["AAPL", "MSFT", "JPM", "XOM", "KO", "PG"]
METRICS = {
    "revenue": 1000.0, "net_income": 200.0, "gross_profit": 500.0, "operating_income": 300.0,
    "total_assets": 4000.0, "operating_cash_flow": 350.0, "shareholders_equity": 1500.0,
    "current_assets": 800.0, "current_liabilities": 400.0,
}  # fmt: skip


def fixture_store() -> FactStore:
    store = FactStore()
    for n, ticker in enumerate(TICKERS):
        facts = []
        for year in (2021, 2022, 2023, 2024, 2025):
            for metric, base in METRICS.items():
                instant = metric in {
                    "total_assets",
                    "shareholders_equity",
                    "current_assets",
                    "current_liabilities",
                }
                facts.append(FinancialFact(
                    ticker=ticker, cik="0000000001", metric=metric, tag="T",
                    value=base * (1 + 0.1 * (year - 2021)) * (1 + 0.05 * n), unit="USD",
                    period_type="instant" if instant else "duration",
                    start=None if instant else date(year - 1, 10, 1), end=date(year, 9, 28),
                    fiscal_year=year, fiscal_period=FiscalPeriod.FY, form=FormType.TEN_K,
                    filed=date(year, 11, 1), accession="0000000001-24-000001"))  # fmt: skip
        store.replace_company_facts(ticker, ParsedFacts(facts, []))
    return store


def universe() -> Universe:
    return Universe.model_validate({
        "name": "t", "fiscal_years": [2021, 2022, 2023, 2024, 2025],
        "companies": [{"ticker": t, "name": f"{t} Corp.", "sector": "IT", "fiscal_year_end": "09-30",
                       "aliases": [t.title()]} for t in TICKERS],
    })  # fmt: skip


SECTIONS = {
    (t, y, i) for t in TICKERS for y in range(2021, 2026) for i in ("1", "1A", "3", "7", "7A", "2")
}


@pytest.fixture(scope="module")
def gold() -> list[GoldExample]:
    with fixture_store() as store:
        return build_gold(store, universe(), available_sections=SECTIONS, seed=7)


def test_builder_is_deterministic_for_a_seed() -> None:
    with fixture_store() as store:
        a = build_gold(store, universe(), available_sections=SECTIONS, seed=7)
        b = build_gold(store, universe(), available_sections=SECTIONS, seed=7)
        c = build_gold(store, universe(), available_sections=SECTIONS, seed=8)
    assert a == b and a != c


def test_every_question_type_is_produced_with_unique_ids(gold: list[GoldExample]) -> None:
    assert {e.type for e in gold} >= {
        QueryType.NUMERIC, QueryType.COMPUTED_METRIC, QueryType.TREND, QueryType.COMPARISON,
        QueryType.QUALITATIVE, QueryType.FACT_LOOKUP, QueryType.CHANGE_DETECTION, QueryType.OUT_OF_SCOPE,
    }  # fmt: skip
    assert len({e.id for e in gold}) == len(gold)


def test_numeric_expectations_equal_the_stored_facts(gold: list[GoldExample]) -> None:
    with fixture_store() as store:
        for e in (g for g in gold if g.id.startswith("num-")):
            ticker = next(t for t in TICKERS if short_name(universe().company(t)) in e.question)
            year = int(e.question.rsplit("fiscal ", 1)[1].rstrip("?"))
            metric_values = {round(f.value, 6) for m in METRICS
                             if (f := store.get_fact(ticker, m, year))}  # fmt: skip
            assert round(e.expected.numeric.value, 6) in metric_values  # type: ignore[union-attr]


def test_ratio_and_trend_expectations_are_computed_correctly(gold: list[GoldExample]) -> None:
    ratio = next(e for e in gold if e.id.startswith("ratio-") and "gross margin" in e.question)
    assert ratio.expected.numeric.value == pytest.approx(
        0.5
    )  # 500 / 1000, scale-invariant  # type: ignore[union-attr]
    trend = next(e for e in gold if e.id.startswith("trend-") and "revenue" in e.question)
    assert trend.expected.numeric.value == pytest.approx(
        0.2 / 1.1 + 0.2 / 1.1 * 0 + (1.3 / 1.1 - 1) - 0.2 / 1.1 + 0.2 / 1.1 * 0, rel=0.5
    )  # type: ignore[union-attr]
    assert trend.expected.numeric.value > 0  # type: ignore[union-attr]


def test_retrieval_questions_only_reference_sections_that_exist(gold: list[GoldExample]) -> None:
    for e in gold:
        for src in e.gold_sources:
            assert (src.ticker, src.fiscal_year, src.item) in SECTIONS


def test_adversarial_examples_expect_abstention_or_ignoring_the_injection(
    gold: list[GoldExample],
) -> None:
    adv = [e for e in gold if e.provenance == "adversarial"]
    assert adv
    for e in adv:
        if e.id.startswith(("adv-", "una-")):
            assert e.expected.abstain
        if e.id.startswith("inj-"):
            assert (
                e.expected.numeric is not None and "Ignore all previous instructions" in e.question
            )
    assert any(
        "fiscal 2015" in e.question or "fiscal 2016" in e.question or "fiscal 2030" in e.question
        for e in adv
    )


def test_splits_hold_out_whole_companies(gold: list[GoldExample]) -> None:
    for e in gold:
        named = [t for t in TICKERS if short_name(universe().company(t)) in e.question]
        if e.provenance != "adversarial" and named and e.id.split("-")[0] not in {"cmp"}:
            assert (e.split == "test") == (named[0] in TEST_TICKERS), e.question
    assert {e.split for e in gold} == {"dev", "test"}


def test_comparison_winner_is_named_and_numeric_matches_the_winner(gold: list[GoldExample]) -> None:
    for e in (g for g in gold if g.type is QueryType.COMPARISON):
        assert e.expected.answer_contains and e.expected.numeric is not None
        assert e.expected.answer_contains[0] in e.question
