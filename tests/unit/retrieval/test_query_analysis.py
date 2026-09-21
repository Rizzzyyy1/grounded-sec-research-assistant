"""Rule-based query understanding."""

from __future__ import annotations

from pathlib import Path

import pytest

from finsight.config.universe import load_universe
from finsight.core.schemas import FormType, QueryType
from finsight.retrieval.query_analysis import QueryAnalyzer, to_filters

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def qa() -> QueryAnalyzer:
    root = Path(__file__).resolve().parents[3]
    return QueryAnalyzer(load_universe(root / "configs" / "universe.yaml"))


@pytest.mark.parametrize(
    ("question", "qtype", "tickers", "years", "metrics"),
    [
        ("What supply chain risks does Apple cite?", QueryType.QUALITATIVE, ("AAPL",), (), ()),
        ("What was NVDA revenue in FY2024?", QueryType.NUMERIC, ("NVDA",), (2024,), ("revenue",)),
        ("What was JPM's ROE in 2023?", QueryType.COMPUTED_METRIC, ("JPM",), (2023,), ("roe",)),
        ("How has Microsoft's operating margin changed since 2021?", QueryType.TREND, ("MSFT",), (2021,), ("operating_margin",)),
        ("Compare AMZN and Walmart gross margin", QueryType.COMPARISON, ("AMZN", "WMT"), (), ("gross_margin",)),
        ("What's new in Exxon's risk factors vs last year?", QueryType.CHANGE_DETECTION, ("XOM",), (), ()),
        ("Should I buy Tesla stock?", QueryType.OUT_OF_SCOPE, ("TSLA",), (), ()),
        ("Why did Apple's gross margin change in fiscal 2023?", QueryType.QUALITATIVE, ("AAPL",), (2023,), ("gross_margin",)),
        ("Who is Alphabet's auditor?", QueryType.FACT_LOOKUP, ("GOOGL",), (), ()),
        ("How much does KO pay in dividends?", QueryType.NUMERIC, ("KO",), (), ("dividends_paid",)),
        ("Is Amazon a good investment?", QueryType.OUT_OF_SCOPE, ("AMZN",), (), ()),
        ("What is the price target for NVDA?", QueryType.OUT_OF_SCOPE, ("NVDA",), (), ()),
    ],
)  # fmt: skip
def test_classification_and_extraction(
    qa: QueryAnalyzer, question: str, qtype: QueryType, tickers: tuple[str, ...],
    years: tuple[int, ...], metrics: tuple[str, ...],
) -> None:  # fmt: skip
    a = qa.analyze(question)
    assert a.query_type is qtype
    assert a.tickers == tickers
    assert a.fiscal_years == years
    assert a.metrics == metrics


def test_advice_takes_priority_over_everything_else(qa: QueryAnalyzer) -> None:
    assert (
        qa.analyze("Compare AAPL and MSFT - which stock should I buy?").query_type
        is QueryType.OUT_OF_SCOPE
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Apple and Microsoft", ("AAPL", "MSFT")),  # order of first mention
        ("microsoft versus apple", ("MSFT", "AAPL")),
        ("AAPL vs MSFT", ("AAPL", "MSFT")),
        ("Google's cloud business", ("GOOGL",)),
        ("J&J litigation", ("JNJ",)),
        ("Procter & Gamble and P&G", ("PG",)),  # aliases of one company collapse
        ("What does KO say?", ("KO",)),
        ("ko is not a ticker here", ()),  # bare symbols only match in upper case
        ("The PGA tour and apples", ()),  # no substring matches
        ("AWS growth", ("AMZN",)),
    ],
)
def test_ticker_extraction(qa: QueryAnalyzer, question: str, expected: tuple[str, ...]) -> None:
    assert qa.tickers(question) == expected


@pytest.mark.parametrize(
    ("question", "years"),
    [
        ("revenue in 2024", (2024,)),
        ("FY2023 results", (2023,)),
        ("fiscal year 2022", (2022,)),
        ("FY24 revenue", (2024,)),
        ("between 2021 and 2023", (2021, 2022, 2023)),
        ("2021-2023 growth", (2021, 2022, 2023)),
        ("from 2022 to 2024", (2022, 2023, 2024)),
        ("2019 and 2024", (2019, 2020, 2021, 2022, 2023, 2024)),
        ("no years here", ()),
        ("3000 employees, 12345 units", ()),
    ],
)
def test_fiscal_year_extraction(qa: QueryAnalyzer, question: str, years: tuple[int, ...]) -> None:
    assert qa.fiscal_years(question) == years


def test_forms_and_items(qa: QueryAnalyzer) -> None:
    assert qa.forms("the 10-K and the quarterly report") == (FormType.TEN_K, FormType.TEN_Q)
    assert qa.forms("Q2 10-Q") == (FormType.TEN_Q,)
    assert qa.forms("annual report") == (FormType.TEN_K,)
    assert qa.forms("nothing") == ()
    assert qa.items("What legal proceedings and cybersecurity issues") == ("3", "1C")
    assert qa.items("liquidity and capital resources in MD&A") == ("7",)


def test_metric_extraction_prefers_specific_over_generic(qa: QueryAnalyzer) -> None:
    assert qa.metrics("gross margin and net income") == ("gross_margin", "net_income")
    assert qa.metrics("free cash flow and capex") == ("free_cash_flow", "capex")
    assert qa.metrics("nothing financial") == ()


def test_to_filters_uses_tickers_years_forms_but_not_item_hints_by_default(
    qa: QueryAnalyzer,
) -> None:
    a = qa.analyze("What legal proceedings did Exxon disclose in its 2023 10-K?")
    f = to_filters(a)
    assert (f.tickers, f.fiscal_years, f.forms, f.items) == (
        ("XOM",),
        (2023,),
        (FormType.TEN_K,),
        (),
    )
    assert to_filters(a, use_item_hints=True).items == ("3",)
    assert to_filters(qa.analyze("what is inflation")).is_empty


@pytest.mark.parametrize(
    "advice",
    [
        "Is Amazon a good investment?",
        "Would Apple be a good buy right now?",
        "Is Tesla overvalued?",
        "Which are the best stocks to buy in tech?",
        "Should I invest in Walmart?",
        "What is Nvidia's price target?",
        "Will the stock rise next quarter?",
    ],
)
def test_investment_advice_and_valuation_opinions_are_out_of_scope(
    qa: QueryAnalyzer, advice: str
) -> None:
    assert qa.analyze(advice).query_type is QueryType.OUT_OF_SCOPE


@pytest.mark.parametrize(
    "factual",
    [
        "What was Apple's net income in 2023?",
        "Does Microsoft describe good governance practices?",  # 'good' but not investment advice
        "How does Tesla value its inventory?",
        "What investments did Alphabet make in 2023?",
    ],
)
def test_factual_questions_are_not_mistaken_for_advice(qa: QueryAnalyzer, factual: str) -> None:
    assert qa.analyze(factual).query_type is not QueryType.OUT_OF_SCOPE


@pytest.mark.parametrize(
    "advice",
    [
        # found by the natural-phrasing probe (gold_v2_draft): all slipped through the first patterns
        "Is now a good time to load up on Nvidia shares?",
        "Would you recommend Tesla to a long-term investor?",
        "Do you think Amazon's stock will beat the market next year?",
        "Which energy company is the best one to buy right now?",
        "Would you suggest buying Apple before earnings?",
        "Is Walmart a safe bet for retirees?",
        "Do you recommend Microsoft to investors?",
    ],
)
def test_natural_phrasings_of_investment_advice_are_out_of_scope(
    qa: QueryAnalyzer, advice: str
) -> None:
    assert qa.analyze(advice).query_type is QueryType.OUT_OF_SCOPE


@pytest.mark.parametrize(
    "factual",
    [
        "How many shares did Apple repurchase in fiscal 2024?",
        "What stock-based compensation did Nvidia record in fiscal 2025?",
        "What was Apple's best-selling product category in fiscal 2024?",
        "Which segment was the best performing for Microsoft in fiscal 2024?",
        "What time period does Tesla's 10-K cover?",
        "Would you summarise the main risk factors Amazon lists?",
        "What does the audit committee recommend about the auditor at Exxon?",
        "Do executives at Walmart sell shares under a trading plan?",
        "What is the fair value of Alphabet's investments in 2023?",
    ],
)
def test_factual_questions_that_share_vocabulary_with_advice_are_not_blocked(
    qa: QueryAnalyzer, factual: str
) -> None:
    """False-positive guard for the broader advice patterns: a refusal here would be a real defect."""
    assert qa.analyze(factual).query_type is not QueryType.OUT_OF_SCOPE
