"""Tools: correct numbers, actionable errors, citation labels."""

from __future__ import annotations

import json

import pytest

from finsight.agent.tools import (
    TOOLS,
    AgentContext,
    SourceRegistry,
    ToolError,
    dispatch,
    money,
    tool_definitions,
)

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
