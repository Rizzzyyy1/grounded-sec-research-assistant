"""Tools: correct numbers, actionable errors, citation labels."""

from __future__ import annotations

import json
from datetime import date

import pytest

from finsight.agent.tools import (
    TOOLS,
    AgentContext,
    SourceRegistry,
    ToolError,
    _register_fact,
    dispatch,
    money,
    tool_definitions,
)
from finsight.core.schemas import FinancialFact, FiscalPeriod, FormType
from finsight.generation.citations import validate_citations
from finsight.generation.context import FactSource

pytestmark = pytest.mark.unit


def call(ctx: AgentContext, name: str, **args: object) -> dict:  # type: ignore[type-arg]
    return json.loads(dispatch(ctx, name, args).text)


def test_get_financial_metric_returns_values_with_provenance(ctx: AgentContext) -> None:
    payload = call(
        ctx, "get_financial_metric", ticker="AAPL", metric="revenue", fiscal_years=[2024]
    )
    (v,) = payload["values"]
    assert v["value"] == 391_035e6 and v["formatted"] == "$391,035 million"
    assert v["xbrl_tag"] == "tag:revenue" and v["accession"] == "0000000001-24-000001"
    assert v["fiscal_period"] == "FY" and v["derived_by_finsight"] is False
    assert payload["label"] == "Revenue"


def test_metric_series_and_default_all_years(ctx: AgentContext) -> None:
    payload = call(ctx, "get_financial_metric", ticker="msft", metric="net_income")
    assert [v["fiscal_year"] for v in payload["values"]] == [2022, 2023, 2024]


def test_stringified_array_arguments_are_accepted_not_rejected(ctx: AgentContext) -> None:
    """Small/local models (observed with Ollama tool calling) sometimes send an array argument
    as a JSON-encoded string, e.g. ``"[2024]"`` instead of ``[2024]``. Claude does not do this,
    but a malformed-yet-recoverable argument should degrade to a correct answer, not a ToolError.
    """
    payload = call(
        ctx, "get_financial_metric", ticker="AAPL", metric="revenue", fiscal_years="[2024]"
    )
    assert [v["fiscal_year"] for v in payload["values"]] == [2024]


def test_bare_string_where_a_list_of_one_ticker_was_meant_is_accepted(
    ctx: AgentContext,
) -> None:
    payload = call(ctx, "search_filings", query="risk factors", tickers="AAPL")
    assert all(p["filing"].startswith("AAPL") for p in payload["passages"])


def test_ratio_tool_shows_formula_and_inputs(ctx: AgentContext) -> None:
    p = call(ctx, "compute_ratio", ticker="AAPL", ratio="gross_margin", fiscal_year=2024)
    assert p["value"] == pytest.approx(180_683 / 391_035) and p["formatted"] == "46.2%"
    assert p["formula"] == "gross_profit / revenue"
    assert {i["metric"] for i in p["inputs"]} == {"gross_profit", "revenue"}


def test_average_basis_ratio_pulls_the_prior_year_input(ctx: AgentContext) -> None:
    p = call(ctx, "compute_ratio", ticker="AAPL", ratio="roe_avg", fiscal_year=2024)
    years = {(i["metric"], i["fiscal_year"]) for i in p["inputs"]}
    assert ("shareholders_equity", 2023) in years and ("shareholders_equity", 2024) in years


def test_bank_without_gross_profit_gets_an_explained_error(ctx: AgentContext) -> None:
    with pytest.raises(ToolError, match=r"JPM has no gross_profit for fiscal 2024"):
        dispatch(
            ctx, "compute_ratio", {"ticker": "JPM", "ratio": "gross_margin", "fiscal_year": 2024}
        )


def test_compare_companies_ranks_best_first_and_skips_with_reasons(ctx: AgentContext) -> None:
    p = call(
        ctx,
        "compare_companies",
        tickers=["AAPL", "MSFT", "JPM"],
        metric="gross_margin",
        fiscal_year=2024,
    )
    assert [r["ticker"] for r in p["ranking"]] == ["MSFT", "AAPL"]  # 69.8% > 46.2%
    assert "JPM" in p["skipped"]
    with pytest.raises(ToolError, match="at least two"):
        dispatch(
            ctx,
            "compare_companies",
            {"tickers": ["AAPL"], "metric": "revenue", "fiscal_year": 2024},
        )
    with pytest.raises(ToolError, match="fewer than two"):
        dispatch(
            ctx,
            "compare_companies",
            {"tickers": ["JPM", "AAPL"], "metric": "gross_margin", "fiscal_year": 2024},
        )


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        ("get_financial_metric", {"ticker": "ZZZ", "metric": "revenue"}, "unknown company"),
        ("get_financial_metric", {"ticker": "AAPL", "metric": "vibes"}, "unknown metric"),
        (
            "get_financial_metric",
            {"ticker": "AAPL", "metric": "roe"},
            "is a ratio, not a reported metric; use compute_ratio",
        ),
        (
            "get_financial_metric",
            {"ticker": "AAPL", "metric": "revenue", "fiscal_years": [1999]},
            "no revenue data",
        ),
        (
            "compute_ratio",
            {"ticker": "AAPL", "ratio": "magic", "fiscal_year": 2024},
            "unknown ratio",
        ),
        ("compute_ratio", {"ticker": "AAPL", "ratio": "roe"}, "missing required argument"),
        (
            "compute_ratio",
            {"ticker": "AAPL", "ratio": "roe", "fiscal_year": "abc"},
            "invalid arguments",
        ),
        ("nope", {}, "unknown tool"),
    ],
)
def test_errors_are_actionable_for_the_model(
    ctx: AgentContext, name: str, args: dict, message: str
) -> None:  # type: ignore[type-arg]
    with pytest.raises(ToolError, match=message):
        dispatch(ctx, name, args)


def test_search_labels_passages_and_labels_are_stable_within_a_run(ctx: AgentContext) -> None:
    first = call(
        ctx,
        "search_filings",
        query="China mainland manufacturing",
        tickers=["AAPL"],
        fiscal_years=[2024],
    )
    labels = [p["source_id"] for p in first["passages"]]
    assert labels[0] == "S1" and all(
        p["filing"].startswith("AAPL 10-K FY2024") for p in first["passages"]
    )
    again = call(
        ctx,
        "search_filings",
        query="China mainland manufacturing",
        tickers=["AAPL"],
        fiscal_years=[2024],
    )
    assert [p["source_id"] for p in again["passages"]] == labels  # same chunk, same label
    assert len(ctx.registry.context().sources) == len(set(labels))


def test_search_with_no_match_says_so(ctx: AgentContext) -> None:
    with pytest.raises(ToolError, match="no passages"):
        dispatch(
            ctx, "search_filings", {"query": "zzzqqq", "tickers": ["AAPL"], "fiscal_years": [2000]}
        )


def test_risk_factor_changes_finds_added_and_removed(ctx: AgentContext) -> None:
    p = call(ctx, "get_risk_factor_changes", ticker="AAPL", fiscal_year=2024)
    assert p["compared_with"] == 2023
    assert any("artificial intelligence" in a["text"] for a in p["added"])
    assert any("tax law" in r["text"] for r in p["removed"])
    assert p["summary"]["unchanged"] >= 1
    assert all(item["source_id"].startswith("S") for item in p["added"] + p["removed"])
    with pytest.raises(ToolError, match="not available for"):
        dispatch(ctx, "get_risk_factor_changes", {"ticker": "MSFT", "fiscal_year": 2024})


# ------------------------------------------------------------------ citation provenance
def test_direct_metric_gets_a_resolvable_fact_citation(ctx: AgentContext) -> None:
    payload = call(
        ctx, "get_financial_metric", ticker="AAPL", metric="revenue", fiscal_years=[2024]
    )
    (v,) = payload["values"]
    sid = v["source_id"]
    assert sid is not None
    source = ctx.registry.context().get(sid)
    assert isinstance(source, FactSource)
    assert (source.ticker, source.fiscal_year, source.metric) == ("AAPL", 2024, "revenue")
    assert source.url == "https://example.com/0000000001-24-000001"

    report = validate_citations(
        f"Apple's revenue was $391,035 million [{sid}].", ctx.registry.context()
    )
    (citation,) = report.citations
    assert (
        citation.kind == "fact"
        and citation.metric == "revenue"
        and citation.xbrl_tag == "tag:revenue"
    )
    assert citation.url == source.url and "$391,035 million" in citation.quote
    assert report.invalid_ids == ()


def test_ratio_retains_every_input_source_and_a_ready_made_citation(ctx: AgentContext) -> None:
    payload = call(ctx, "compute_ratio", ticker="AAPL", ratio="gross_margin", fiscal_year=2024)
    ids = [i["source_id"] for i in payload["inputs"]]
    assert all(i is not None for i in ids) and len(set(ids)) == 2  # revenue + gross_profit
    assert {i["metric"] for i in payload["inputs"]} == {"revenue", "gross_profit"}

    cite_as = payload["cite_as"]
    assert cite_as is not None
    report = validate_citations(
        f"Apple's gross margin was 46.2% {cite_as}.", ctx.registry.context()
    )
    assert len(report.citations) == 2
    assert {c.metric for c in report.citations} == {"revenue", "gross_profit"}
    assert all(c.kind == "fact" for c in report.citations) and report.invalid_ids == ()


def test_average_basis_ratio_gives_each_year_its_own_citation(ctx: AgentContext) -> None:
    """roe_avg needs shareholders_equity from *two* fiscal years - two different filings - so
    citing the ratio must retain a distinct, separately resolvable source per year, not one
    citation papering over both."""
    payload = call(ctx, "compute_ratio", ticker="AAPL", ratio="roe_avg", fiscal_year=2024)
    equity_rows = [i for i in payload["inputs"] if i["metric"] == "shareholders_equity"]
    years_to_ids = {(r["fiscal_year"], r["source_id"]) for r in equity_rows}
    assert {y for y, _sid in years_to_ids} == {2023, 2024}
    ids = {sid for _year, sid in years_to_ids}
    assert None not in ids and len(ids) == 2  # 2023 and 2024 never collide on one label

    context = ctx.registry.context()
    for _year, sid in years_to_ids:
        source = context.get(sid)
        assert isinstance(source, FactSource) and source.fiscal_year == _year


def test_compare_companies_gives_each_ranked_company_a_resolvable_citation(
    ctx: AgentContext,
) -> None:
    payload = call(
        ctx, "compare_companies", tickers=["AAPL", "MSFT"], metric="revenue", fiscal_year=2024
    )
    assert len(payload["ranking"]) == 2
    context = ctx.registry.context()
    for row in payload["ranking"]:
        assert row["cite_as"] is not None
        report = validate_citations(
            f"{row['ticker']} revenue was {row['formatted']} {row['cite_as']}.", context
        )
        (citation,) = report.citations
        assert citation.ticker == row["ticker"] and citation.kind == "fact"


def test_compare_companies_with_a_ratio_cites_every_underlying_fact_per_company(
    ctx: AgentContext,
) -> None:
    payload = call(
        ctx, "compare_companies", tickers=["AAPL", "MSFT"], metric="gross_margin", fiscal_year=2024
    )
    context = ctx.registry.context()
    for row in payload["ranking"]:
        report = validate_citations(
            f"{row['ticker']} margin was {row['formatted']} {row['cite_as']}.", context
        )
        assert len(report.citations) == 2  # revenue + gross_profit for that one company
        assert all(c.ticker == row["ticker"] for c in report.citations)


def test_compare_companies_ratio_keeps_the_value_unambiguous_with_no_citation(
    ctx: AgentContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: compare_companies used to put a bare `"cite_as": null` next to an otherwise
    normal ranking row, with no formula, no inputs and no explanation. A live Ollama run
    (llama3.2:3b) read that null as "the value itself is missing" and answered "not available"
    for a company whose number the tool had in fact returned correctly - reproduced directly
    against ResearchAgent.answer() outside the eval harness. The fix separates "the value" from
    "the citation" so plainly that misreading one as the other should no longer be possible: value
    and formatted stay present, the formula and every input fact are shown per company, and an
    explicit note spells out what a null cite_as does and does not mean."""
    real_filing_url = ctx.facts.filing_url
    # Simulate JPM's fiscal-2024 filing never having been catalogued (see conftest._accession).
    monkeypatch.setattr(
        ctx.facts, "filing_url",
        lambda acc: None if acc.startswith("0000000003-24") else real_filing_url(acc),
    )  # fmt: skip

    payload = call(
        ctx, "compare_companies", tickers=["AAPL", "JPM"], metric="net_margin", fiscal_year=2024
    )
    jpm_row = next(r for r in payload["ranking"] if r["ticker"] == "JPM")
    aapl_row = next(r for r in payload["ranking"] if r["ticker"] == "AAPL")

    # The number is present and correct regardless of citation availability.
    assert jpm_row["value"] == pytest.approx(58_471e6 / 177_556e6, rel=1e-6)
    assert jpm_row["formatted"] and jpm_row["cite_as"] is None
    # Full visibility into why: the formula and every contributing fact, each explicitly uncited.
    assert payload["formula"] == "net_income / revenue"
    assert {i["metric"] for i in jpm_row["inputs"]} == {"net_income", "revenue"}
    assert all(i["source_id"] is None for i in jpm_row["inputs"])
    # The note makes the value/citation distinction explicit, not left for the model to infer.
    assert "null" in payload["note"] and "not" in payload["note"].lower()
    # The unaffected company is unaffected: still cited normally.
    assert aapl_row["cite_as"] is not None


def test_search_filings_passage_resolves_to_a_passage_citation(ctx: AgentContext) -> None:
    payload = call(
        ctx, "search_filings", query="China mainland manufacturing", tickers=["AAPL"],
        fiscal_years=[2024],
    )  # fmt: skip
    sid = payload["passages"][0]["source_id"]
    report = validate_citations(
        f"Apple relies on outsourcing partners [{sid}].", ctx.registry.context()
    )
    (citation,) = report.citations
    assert citation.kind == "passage" and citation.chunk_id is not None and citation.item == "1A"
    assert citation.metric is None and citation.xbrl_tag is None  # fact-only fields stay unset


def test_fact_with_no_catalogued_filing_gets_no_citation_label(ctx: AgentContext) -> None:
    """`_register_fact` must never fabricate a source: a fact whose accession has no row in the
    `filings` table (e.g. a facts-only ingest that never downloaded the document) gets no
    citation label at all, not a guessed or broken URL."""
    orphan = FinancialFact(
        ticker="AAPL", cik="0000000001", metric="revenue", tag="tag:revenue", value=1.0,
        unit="USD", period_type="duration", start=date(2020, 1, 1), end=date(2020, 12, 31),
        fiscal_year=2020, fiscal_period=FiscalPeriod.FY, form=FormType.TEN_K,
        filed=date(2020, 11, 1), accession="9999999999-20-000099",
    )  # fmt: skip
    assert _register_fact(ctx, orphan) is None
    assert ctx.registry.context().sources == ()  # nothing was registered under a guessed id either


def test_invalid_and_repeated_ids_are_reported_across_a_mixed_passage_and_fact_registry(
    ctx: AgentContext,
) -> None:
    """The same run can register both fact and passage sources; an id the model invents (never
    handed out by either kind of tool) must still be caught, and a real one still resolves."""
    call(ctx, "get_financial_metric", ticker="AAPL", metric="revenue", fiscal_years=[2024])
    call(
        ctx,
        "search_filings",
        query="China mainland manufacturing",
        tickers=["AAPL"],
        fiscal_years=[2024],
    )
    context = ctx.registry.context()
    assert {type(s).__name__ for s in context.sources} == {"FactSource", "Source"}

    text = "Real fact [S1]. Real passage [S2]. Invented [S99]. Repeated real fact [S1, S1]."
    report = validate_citations(text, context)
    assert report.invalid_ids == ("S99",)
    assert {c.source_id for c in report.citations} == {"S1", "S2"}


def test_evidence_contains_the_figures_a_correct_answer_may_state(ctx: AgentContext) -> None:
    out = dispatch(
        ctx, "get_financial_metric", {"ticker": "AAPL", "metric": "revenue", "fiscal_years": [2024]}
    )
    assert "$391,035 million" in out.evidence
    ratio = dispatch(
        ctx, "compute_ratio", {"ticker": "AAPL", "ratio": "gross_margin", "fiscal_year": 2024}
    )
    assert "46.2%" in ratio.evidence


def test_tool_definitions_are_valid_and_strict_about_arguments() -> None:
    defs = tool_definitions()
    assert [d["name"] for d in defs] == [t.name for t in TOOLS]
    for d in defs:
        schema = d["input_schema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        assert d["description"]


def test_money_formatting_and_registry() -> None:
    assert money(391_035e6) == "$391,035 million" and money(6.08, "USD/shares") == "$6.08"
    assert SourceRegistry().context().sources == ()


def test_parallel_tool_calls_share_one_database_connection_safely(ctx: AgentContext) -> None:
    """Regression: the DuckDB lock was released between queries, so concurrent calls corrupted each other."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    def one(i: int) -> float:
        t = ("AAPL", "MSFT", "JPM")[i % 3]
        return call(ctx, "get_financial_metric", ticker=t, metric="revenue", fiscal_years=[2024])[
            "values"
        ][0]["value"]

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(one, range(96)))
    assert results[0] == 391_035e6 and results[1] == 245_122e6 and results[2] == 177_556e6
    assert all(r == results[i % 3] for i, r in enumerate(results))


def test_concurrent_registration_never_hands_out_a_colliding_source_id(ctx: AgentContext) -> None:
    """Regression: SourceRegistry mutated two plain dicts with no lock of its own, so two threads
    computing _next_id() from the same pre-mutation lengths could label two different facts with
    the same id - a citation would then silently resolve to the wrong source. 96 calls across 3
    tickers should end up with exactly 3 distinct fact sources, one per (ticker, metric, year)."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    def one(i: int) -> str | None:
        t = ("AAPL", "MSFT", "JPM")[i % 3]
        return call(ctx, "get_financial_metric", ticker=t, metric="revenue", fiscal_years=[2024])[
            "values"
        ][0]["source_id"]

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(one, range(96)))
    assert all(i is not None for i in ids)
    assert len(set(ids)) == 3  # one id per ticker, however many threads raced to register it
    context = ctx.registry.context()
    assert len(context.sources) == 3
    assert len({s.id for s in context.sources}) == 3  # no two distinct sources share a label
